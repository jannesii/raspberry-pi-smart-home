#include "LedManager.h"

// Internal helper to convert ms to ticks safely
static inline TickType_t MS2T(uint32_t ms) {
    return pdMS_TO_TICKS(ms);
}

LedManager::LedManager(uint8_t pin, bool activeHigh)
    : pin_(pin), activeHigh_(activeHigh) {}

void LedManager::begin(uint16_t pulseOnMs, uint16_t pulseOffMs) {
    defaultOnMs_  = pulseOnMs;
    defaultOffMs_ = pulseOffMs;

    pinMode(pin_, OUTPUT);
    setLed(false);

    // Create a small command queue; patterns are short so 8 slots is enough
    queue_ = xQueueCreate(8, sizeof(Command));

    // Create worker task
    xTaskCreate(&LedManager::taskEntry, "LedMgr", 2048, this, 1, &task_);
}

void LedManager::blinkSingle() {
    enqueue(PatternType::Single, defaultOnMs_, defaultOffMs_);
}

void LedManager::blinkDouble() {
    enqueue(PatternType::Double, defaultOnMs_, defaultOffMs_);
}

void LedManager::blinkTriple() {
    enqueue(PatternType::Triple, defaultOnMs_, defaultOffMs_);
}

void LedManager::rapidBurst() {
    // Slightly faster pulses for the burst
    enqueue(PatternType::RapidBurst, (uint16_t)(defaultOnMs_ * 0.6f), (uint16_t)(defaultOffMs_ * 0.6f));
}

void LedManager::repeatDouble(uint32_t everyMs, uint32_t totalDurationMs) {
    startRepeat(PatternType::Double, everyMs, totalDurationMs);
}

void LedManager::repeatTriple(uint32_t everyMs, uint32_t totalDurationMs) {
    startRepeat(PatternType::Triple, everyMs, totalDurationMs);
}

void LedManager::cancelRepeats() {
    if (repeatTimer_) {
        xTimerStop(repeatTimer_, 0);
    }
    repeatActive_ = false;
    repeatPattern_ = PatternType::None;
    repeatEndsAtMs_ = 0;
}

void LedManager::enqueue(PatternType type, uint16_t onMs, uint16_t offMs) {
    if (!queue_) return;
    Command cmd{type, onMs, offMs};
    // Best-effort enqueue; drop if full to avoid blocking callers
    xQueueSend(queue_, &cmd, 0);
}

void LedManager::taskEntry(void* arg) {
    auto* self = static_cast<LedManager*>(arg);
    self->taskLoop();
}

void LedManager::taskLoop() {
    Command cmd;
    for (;;) {
        if (xQueueReceive(queue_, &cmd, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        playCommand(cmd);
    }
}

void LedManager::playCommand(const Command& cmd) {
    auto playPulse = [&](uint16_t onMs, uint16_t offMs){
        setLed(true);
        vTaskDelay(MS2T(onMs));
        setLed(false);
        vTaskDelay(MS2T(offMs));
    };

    switch (cmd.type) {
        case PatternType::Single:
            playPulse(cmd.onMs, cmd.offMs);
            break;
        case PatternType::Double:
            playPulse(cmd.onMs, cmd.offMs);
            playPulse(cmd.onMs, (uint16_t)(cmd.offMs * 2));
            break;
        case PatternType::Triple:
            playPulse(cmd.onMs, cmd.offMs);
            playPulse(cmd.onMs, cmd.offMs);
            playPulse(cmd.onMs, (uint16_t)(cmd.offMs * 2));
            break;
        case PatternType::RapidBurst: {
            // ~0.6–1.0s total: 6 quick pulses
            const uint8_t pulses = 6;
            for (uint8_t i = 0; i < pulses; ++i) {
                playPulse(cmd.onMs, (i + 1 < pulses) ? cmd.offMs : (uint16_t)(cmd.offMs * 2));
            }
            break;
        }
        case PatternType::None:
        default:
            break;
    }
}

void LedManager::repeatTimerCbStatic(TimerHandle_t h) {
    void* id = pvTimerGetTimerID(h);
    auto* self = static_cast<LedManager*>(id);
    if (self) self->repeatTimerCb();
}

void LedManager::repeatTimerCb() {
    if (!repeatActive_) return;
    uint32_t now = millis();
    if (repeatEndsAtMs_ != 0 && (int32_t)(now - repeatEndsAtMs_) >= 0) {
        cancelRepeats();
        return;
    }
    // Post the chosen repeating pattern to the queue; use defaults
    switch (repeatPattern_) {
        case PatternType::Double:
            enqueue(PatternType::Double, defaultOnMs_, defaultOffMs_);
            break;
        case PatternType::Triple:
            enqueue(PatternType::Triple, defaultOnMs_, defaultOffMs_);
            break;
        default:
            break;
    }
}

void LedManager::startRepeat(PatternType pat, uint32_t everyMs, uint32_t totalDurationMs) {
    if (everyMs < 50) everyMs = 50;  // sane minimum
    repeatPattern_ = pat;
    repeatEndsAtMs_ = (totalDurationMs == 0) ? 0 : (millis() + totalDurationMs);

    if (!repeatTimer_) {
        repeatTimer_ = xTimerCreate("LedRpt", MS2T(everyMs), pdTRUE, this, &LedManager::repeatTimerCbStatic);
    }

    if (!repeatTimer_) return;

    // Change period and start (or restart)
    xTimerChangePeriod(repeatTimer_, MS2T(everyMs), 0);
    repeatActive_ = true;
    xTimerStart(repeatTimer_, 0);
}


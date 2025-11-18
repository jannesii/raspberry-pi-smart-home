#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <freertos/timers.h>

// LedManager: non-blocking LED pattern player backed by a FreeRTOS task.
//
// Patterns provided (matching proposed UX):
//  - blinkSingle():    One short blink
//  - blinkDouble():    Two short blinks
//  - blinkTriple():    Three short blinks
//  - rapidBurst():     Rapid burst (~0.6–1.0s total)
//  - repeatDouble():   Double blink repeated at low cadence for a duration
//  - repeatTriple():   Triple blink repeated at low cadence for a duration
//  - cancelRepeats():  Stop any active repeating schedule
//
// Use begin() once from setup() to start the task.
// Calls are thread-safe from any task context.
class LedManager {
public:
    explicit LedManager(uint8_t pin, bool activeHigh = true);

    void begin(uint16_t pulseOnMs = 80, uint16_t pulseOffMs = 80);

    // One-shot patterns
    void blinkSingle();
    void blinkDouble();
    void blinkTriple();
    void rapidBurst();

    // Time-bounded repeating patterns (e.g., every 30–60s)
    void repeatDouble(uint32_t everyMs, uint32_t totalDurationMs);
    void repeatTriple(uint32_t everyMs, uint32_t totalDurationMs);
    void cancelRepeats();

private:
    enum class PatternType : uint8_t {
        Single,
        Double,
        Triple,
        RapidBurst,
        None
    };

    struct Command {
        PatternType type;
        uint16_t    onMs;   // per pulse ON time (ms)
        uint16_t    offMs;  // per pulse OFF gap (ms)
    };

    static void taskEntry(void*);
    void taskLoop();

    void playCommand(const Command& cmd);
    inline void setLed(bool on) {
        digitalWrite(pin_, (on ^ !activeHigh_) ? HIGH : LOW);
    }
    void enqueue(PatternType type, uint16_t onMs, uint16_t offMs);

    // Repeating scheduler (FreeRTOS software timer)
    static void repeatTimerCbStatic(TimerHandle_t h);
    void repeatTimerCb();

    void startRepeat(PatternType pat, uint32_t everyMs, uint32_t totalDurationMs);

private:
    uint8_t       pin_;
    bool          activeHigh_;
    uint16_t      defaultOnMs_  = 80;
    uint16_t      defaultOffMs_ = 80;

    TaskHandle_t  task_   = nullptr;
    QueueHandle_t queue_  = nullptr;

    // Repeating
    TimerHandle_t repeatTimer_   = nullptr;  // periodic timer
    bool          repeatActive_  = false;
    PatternType   repeatPattern_ = PatternType::None;
    uint32_t      repeatEndsAtMs_ = 0;  // absolute millis() deadline
};


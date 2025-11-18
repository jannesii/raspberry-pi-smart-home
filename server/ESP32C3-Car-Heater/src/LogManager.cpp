// LogManager.cpp
#include "LogManager.h"

LogManager::LogManager()
    : head_(0),
      count_(0)
{
}

String LogManager::makeKey(uint16_t index) const {
    char buf[8];
    // e000 .. e255
    snprintf(buf, sizeof(buf), "e%03u", static_cast<unsigned>(index));
    return String(buf);
}

bool LogManager::begin() {
    if (!prefs_.begin(NAMESPACE, /*readOnly*/ false)) {
        Serial.println(F("[LogManager] Failed to open NVS namespace 'logs'"));
        return false;
    }

    head_  = prefs_.getUShort(KEY_HEAD, 0);
    count_ = prefs_.getUShort(KEY_COUNT, 0);
    if (head_ >= MAX_ENTRIES) head_ = 0;
    if (count_ >  MAX_ENTRIES) count_ = MAX_ENTRIES;

    Serial.printf("[LogManager] head=%u, count=%u, capacity=%u\n",
                  head_, count_, MAX_ENTRIES);
    return true;
}

void LogManager::append(const String& line) {
    // If begin() failed, prefs_ may not be open – you could guard if needed.
    uint16_t index = head_;  // slot to write
    String key = makeKey(index);

    prefs_.putString(key.c_str(), line);

    head_ = static_cast<uint16_t>((head_ + 1) % MAX_ENTRIES);
    if (count_ < MAX_ENTRIES) {
        count_++;
    }

    prefs_.putUShort(KEY_HEAD, head_);
    prefs_.putUShort(KEY_COUNT, count_);
    // prefs_ stays open; NVS has wear-levelling, but don't go totally crazy
    if (callback_) {
        callback_(line);
    }
}

void LogManager::dumpToSerial() const {
    Serial.println(F("[LogManager] Dumping logs (oldest -> newest)"));

    if (count_ == 0) {
        Serial.println(F("[LogManager] (no entries)"));
        return;
    }

    // Oldest entry is at: (head_ - count_ + MAX_ENTRIES) % MAX_ENTRIES
    uint16_t start = (head_ + MAX_ENTRIES - count_) % MAX_ENTRIES;

    for (uint16_t i = 0; i < count_; ++i) {
        uint16_t idx = static_cast<uint16_t>((start + i) % MAX_ENTRIES);
        String key = makeKey(idx);
        String line = prefs_.getString(key.c_str(), "");

        Serial.printf("[%3u] %s\n", static_cast<unsigned>(idx), line.c_str());
    }
}

String LogManager::toStringNewestFirst(uint16_t maxLines) const {
    if (count_ == 0) {
        // empty string → caller can replace with "No log entries" text
        return String();
    }

    // How many lines we will actually return
    uint16_t lines = count_;
    if (maxLines != 0 && maxLines < lines) {
        lines = maxLines;
    }

    // Rough reserve to avoid many reallocations (tune if you like)
    String out;
    out.reserve(lines * 64);

    // head_ points to the *next* write position.
    // Newest entry is at (head_ - 1 + MAX_ENTRIES) % MAX_ENTRIES.
    int32_t idx = static_cast<int32_t>(head_) - 1;
    if (idx < 0) idx += MAX_ENTRIES;

    for (uint16_t i = 0; i < lines; ++i) {
        String key = makeKey(static_cast<uint16_t>(idx));
        String line = prefs_.getString(key.c_str(), "");

        out += line;
        if (i + 1 < lines) {
            out += '\n';
        }

        // Move backwards through the ring buffer
        idx -= 1;
        if (idx < 0) idx += MAX_ENTRIES;
    }

    return out;
}

void LogManager::clear() {
    // Remove all potential entry keys
    for (uint16_t i = 0; i < MAX_ENTRIES; ++i) {
        String key = makeKey(i);
        prefs_.remove(key.c_str());
    }

    head_  = 0;
    count_ = 0;

    prefs_.putUShort(KEY_HEAD, 0);
    prefs_.putUShort(KEY_COUNT, 0);

    Serial.println(F("[LogManager] Logs cleared"));
}

#pragma once

#include <Arduino.h>

class Poster {
public:
  Poster();

  bool postReading(float temperatureC, float humidityPct);
  bool postError(const String &message);

private:
  bool postJSON(const String &body);
};

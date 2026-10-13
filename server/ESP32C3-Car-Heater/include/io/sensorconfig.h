// Sensor bus defaults; override any of them in core/staticconfig.h.
#pragma once

#include "core/staticconfig.h"

#ifndef SHT45_I2C_ADDRESS
#define SHT45_I2C_ADDRESS 0x44 // Adafruit SHT45 (fixed)
#endif
#ifndef TMP117_GLASS_REFERENCE_I2C_ADDRESS
#define TMP117_GLASS_REFERENCE_I2C_ADDRESS 0x48 // Adafruit TMP117 default
#endif
#ifndef TMP117_GLASS_COMPARISON_I2C_ADDRESS
#define TMP117_GLASS_COMPARISON_I2C_ADDRESS 0x49 // Adafruit TMP117 with ADDR jumper closed
#endif
#ifndef I2C_CLOCK_HZ
#define I2C_CLOCK_HZ 100000 // standard mode for the 1-2 m cable run
#endif

static_assert(TMP117_GLASS_REFERENCE_I2C_ADDRESS != TMP117_GLASS_COMPARISON_I2C_ADDRESS,
              "The two TMP117 glass sensors need distinct I2C addresses");
static_assert(SHT45_I2C_ADDRESS != TMP117_GLASS_REFERENCE_I2C_ADDRESS &&
                  SHT45_I2C_ADDRESS != TMP117_GLASS_COMPARISON_I2C_ADDRESS,
              "The SHT45 address must not collide with a TMP117");

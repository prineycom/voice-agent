// env_knobs.h — SDK-independent env knob parsing for a2f_stream.
//
// Pure-std helpers extracted from main.cpp so they can be unit-tested off the
// SDK toolchain (see test_env_knobs.cpp; runnable on any C++17 compiler).
// Anything touching nva2f/nva2x types stays in main.cpp.
#pragma once

#include <cstddef>
#include <cstdlib>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

// Env knob helpers. Unset or unparseable envs return the given default, so
// every knob falls back to its SDK/model-config baseline — a bad value warns
// and degrades, it never aborts the helper.
inline float envFloat(const char* name, float def) {
  const char* p = std::getenv(name);
  if (!p || !*p) return def;
  char* end = nullptr;
  const float v = std::strtof(p, &end);
  if (end == p || *end != '\0') {
    std::cerr << "a2f_stream: env " << name << "='" << p << "' is not a float — using " << def << "\n";
    return def;
  }
  return v;
}
inline long envInt(const char* name, long def) {
  const char* p = std::getenv(name);
  if (!p || !*p) return def;
  char* end = nullptr;
  const long v = std::strtol(p, &end, 10);
  if (end == p || *end != '\0') {
    std::cerr << "a2f_stream: env " << name << "='" << p << "' is not an integer — using " << def << "\n";
    return def;
  }
  return v;
}

// Parses a "Name=1.5,Other=0.25" CSV env into name→value pairs. Malformed
// entries (no '=', empty name, non-float value) are warned and skipped; blank
// entries are ignored. Unset env ⇒ empty result.
inline std::vector<std::pair<std::string, float>> envCsvMap(const char* name) {
  std::vector<std::pair<std::string, float>> out;
  const char* p = std::getenv(name);
  if (!p) return out;
  const std::string csv(p);
  for (std::size_t pos = 0; pos <= csv.size();) {
    std::size_t comma = csv.find(',', pos);
    if (comma == std::string::npos) comma = csv.size();
    std::string entry = csv.substr(pos, comma - pos);
    pos = comma + 1;
    const auto first = entry.find_first_not_of(" \t");
    if (first == std::string::npos) continue;  // blank entry (incl. trailing comma)
    entry = entry.substr(first, entry.find_last_not_of(" \t") - first + 1);
    const std::size_t eq = entry.find('=');
    std::string key = (eq == std::string::npos) ? std::string() : entry.substr(0, eq);
    const auto keyEnd = key.find_last_not_of(" \t");
    key = (keyEnd == std::string::npos) ? std::string() : key.substr(0, keyEnd + 1);
    if (key.empty()) {
      std::cerr << "a2f_stream: env " << name << ": bad entry '" << entry
                << "' (want Name=value) — skipped\n";
      continue;
    }
    const std::string val = entry.substr(eq + 1);
    char* end = nullptr;
    const float v = std::strtof(val.c_str(), &end);
    while (end && (*end == ' ' || *end == '\t')) ++end;
    if (end == val.c_str() || *end != '\0') {
      std::cerr << "a2f_stream: env " << name << ": bad value in '" << entry << "' — skipped\n";
      continue;
    }
    out.emplace_back(std::move(key), v);
  }
  return out;
}

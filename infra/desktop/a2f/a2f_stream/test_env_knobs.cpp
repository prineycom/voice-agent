// test_env_knobs.cpp — plain C++17 unit tests for env_knobs.h (no framework).
// SDK-independent, so it runs on any box with a C++17 compiler:
//   g++ -std=c++17 -o test_env_knobs test_env_knobs.cpp && ./test_env_knobs
// Prints "ok N tests" on success; any failure aborts via assert().
// Warn lines on stderr are expected — the knobs warn-and-degrade by design.

#include "env_knobs.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

namespace {

int g_tests = 0;

void set(const char* name, const char* value) {
  assert(setenv(name, value, /*overwrite=*/1) == 0);
}
void unset(const char* name) { assert(unsetenv(name) == 0); }

bool near(float a, float b) { return std::fabs(a - b) < 1e-6f; }

void check(bool cond) {
  assert(cond);
  ++g_tests;
}

using CsvMap = std::vector<std::pair<std::string, float>>;

void testEnvFloat() {
  unset("T_F");
  check(near(envFloat("T_F", 2.5f), 2.5f));           // unset → default
  set("T_F", "1.5");
  check(near(envFloat("T_F", 2.5f), 1.5f));           // valid
  set("T_F", "-0.25");
  check(near(envFloat("T_F", 2.5f), -0.25f));         // valid negative
  set("T_F", "garbage");
  check(near(envFloat("T_F", 2.5f), 2.5f));           // garbage → default + warn
  set("T_F", "1.5x");
  check(near(envFloat("T_F", 2.5f), 2.5f));           // trailing junk → default + warn
  set("T_F", "");
  check(near(envFloat("T_F", 2.5f), 2.5f));           // empty string → default
}

void testEnvInt() {
  unset("T_I");
  check(envInt("T_I", 7) == 7);                       // unset → default
  set("T_I", "42");
  check(envInt("T_I", 7) == 42);                      // valid
  set("T_I", "-3");
  check(envInt("T_I", 7) == -3);                      // valid negative
  set("T_I", "garbage");
  check(envInt("T_I", 7) == 7);                       // garbage → default + warn
  set("T_I", "4.2");
  check(envInt("T_I", 7) == 7);                       // float-looking → default + warn
  set("T_I", "");
  check(envInt("T_I", 7) == 7);                       // empty string → default
}

void testEnvCsvMap() {
  unset("T_CSV");
  check(envCsvMap("T_CSV").empty());                  // unset → empty

  set("T_CSV", "");
  check(envCsvMap("T_CSV").empty());                  // empty string → empty

  set("T_CSV", "Name=1.5,Other=0.25");
  {
    const CsvMap m = envCsvMap("T_CSV");
    check(m.size() == 2);
    check(m[0].first == "Name" && near(m[0].second, 1.5f));
    check(m[1].first == "Other" && near(m[1].second, 0.25f));
  }

  set("T_CSV", "Name");                               // no '=' → skipped + warn
  check(envCsvMap("T_CSV").empty());

  set("T_CSV", "Name=");                              // empty value → skipped + warn
  check(envCsvMap("T_CSV").empty());

  set("T_CSV", "=0.5");                               // empty name → skipped + warn
  check(envCsvMap("T_CSV").empty());

  set("T_CSV", "Name=abc");                           // non-float value → skipped + warn
  check(envCsvMap("T_CSV").empty());

  set("T_CSV", "Name=0.5,");                          // trailing comma → blank entry ignored
  {
    const CsvMap m = envCsvMap("T_CSV");
    check(m.size() == 1);
    check(m[0].first == "Name" && near(m[0].second, 0.5f));
  }

  set("T_CSV", ",,Name=0.5,,");                       // multiple blanks ignored
  {
    const CsvMap m = envCsvMap("T_CSV");
    check(m.size() == 1);
    check(m[0].first == "Name" && near(m[0].second, 0.5f));
  }

  set("T_CSV", " Name = 0.5 ");                       // padded entry → trimmed
  {
    const CsvMap m = envCsvMap("T_CSV");
    check(m.size() == 1);
    check(m[0].first == "Name" && near(m[0].second, 0.5f));
  }

  set("T_CSV", "Good=1.0,bad,Also=2.0");              // bad entry skipped, rest kept
  {
    const CsvMap m = envCsvMap("T_CSV");
    check(m.size() == 2);
    check(m[0].first == "Good" && near(m[0].second, 1.0f));
    check(m[1].first == "Also" && near(m[1].second, 2.0f));
  }
}

}  // namespace

int main() {
  testEnvFloat();
  testEnvInt();
  testEnvCsvMap();
  std::printf("ok %d tests\n", g_tests);
  return 0;
}

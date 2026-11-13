Source files in this subdirectory contain unit tests.
They use gTest and gMock for running unit testing.
Unit tests can be launched with "dosbox-x -tests".
Free free to add more unit tests in this directory.

Read Google Test Primer for reference of most available
features, macros, and guidance about writing unit tests:

https://google.github.io/googletest/primer.html

More information about gMock can be found at:

https://google.github.io/googletest/gmock_for_dummies.html

The portable FPU integer conversion regression runs separately:
  python3 tests/fpu_i64_test.py src/fpu/fpu_instructions.h
It needs g++, UBSan, and an x86 host with 80-bit extended long double.
It checks exact integer stores, extended save/restore encoding and arithmetic
continuity against the host x87 representation, including signed boundaries.

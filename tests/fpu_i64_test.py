#!/usr/bin/env python3
"""Exercise the portable FPU's production conversions with a memory/register fixture."""
from pathlib import Path
import subprocess
import argparse
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path, help='dosbox-x/src/fpu/fpu_instructions.h')
args = parser.parse_args()
if not args.source.is_file():
    parser.error(f'{args.source}: missing source')
source = args.source.read_text()
names = ['FROUND', 'FPU_FLD80', 'FPU_ST80', 'FPU_FLD_I64',
         'FPU_ExactI64', 'FPU_FST_I64', 'FPU_FSTT_I64']
functions = []
for name in names:
    if name == 'FPU_ExactI64' and name + '(' not in source:
        continue
    start = source.rfind('static ', 0, source.index(name + '('))
    body = source.index('{', start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    functions.append(source[start:end])
fixture = r'''
#include <cmath>
#include <cstdint>
#include <cstring>
#include <cassert>
#include <cstdio>
#include <limits>
#define LONGTYPE(x) INT64_C(x)
#define ULONGTYPE(x) UINT64_C(x)
#define BIAS80 16383
#define BIAS64 1023
#define TOP 0
const unsigned FPU_Reg_80_exponent_bias = 16383;
using PhysPt = unsigned;
using Bitu = unsigned;
union FPU_Reg { double d; int64_t ll; struct { uint32_t lower; int32_t upper; } l; };
struct FPU_Reg_80 { struct { uint64_t l; uint16_t h; } raw; };
struct FPUControlWord { enum class RoundMode { Nearest, Down, Up, Chop };
    RoundMode RC = RoundMode::Nearest; };
struct { FPU_Reg regs[9]; FPU_Reg_80 regs_80[9]; bool use80[9]; FPUControlWord cw; } fpu;
unsigned char memory[128];
uint32_t mem_readd(unsigned p) { uint32_t v; memcpy(&v, memory+p, 4); return v; }
uint16_t mem_readw(unsigned p) { uint16_t v; memcpy(&v, memory+p, 2); return v; }
void mem_writed(unsigned p, uint32_t v) { memcpy(memory+p, &v, 4); }
void mem_writew(unsigned p, uint16_t v) { memcpy(memory+p, &v, 2); }
void FPU_FPOP() {}
'''
tests = r'''
void check(int64_t value) {
    memcpy(memory, &value, 8);
    FPU_FLD_I64(0, TOP);
    const double direct = fpu.regs[TOP].d;
    FPU_FST_I64(16);
    assert(memcmp(memory, memory+16, 8) == 0);
    FPU_FSTT_I64(16);
    assert(memcmp(memory, memory+16, 8) == 0);
    FPU_ST80(32, TOP, fpu.regs_80[TOP], fpu.use80[TOP]);
    // The host x87 encoding is an independent oracle for all 64 integer bits.
    const long double reference = value;
    assert(sizeof(reference) >= 10);
    assert(memcmp(memory+32, &reference, 10) == 0);
    fpu.regs[TOP].d = FPU_FLD80(32, fpu.regs_80[TOP]);
    assert(fpu.regs[TOP].d == direct);
    assert(fpu.regs[TOP].d * (1.0 / 70000000.0) == direct * (1.0 / 70000000.0));
    FPU_FST_I64(16);
    assert(memcmp(memory, memory+16, 8) == 0);
    FPU_FSTT_I64(16);
    assert(memcmp(memory, memory+16, 8) == 0);
}
int main() {
    static_assert(std::numeric_limits<long double>::digits == 64, "oracle requires x86 80-bit extended long double");
    for (int64_t value : {INT64_C(0), INT64_C(1), INT64_C(-1), INT64_C(123456789),
            INT64_C(-123456789), INT64_C(2147483647), INT64_C(2147483648),
            INT64_C(4294967295), INT64_C(4294967296), INT64_C(9007199254740991),
            INT64_C(9007199254740993), INT64_C(-9007199254740993),
            INT64_MAX, INT64_MIN, INT64_MIN+1}) check(value);
    uint64_t random = 1;
    for (int i = 0; i < 10000; ++i) {
        random = random * UINT64_C(6364136223846793005) + 1;
        check(static_cast<int64_t>(random));
    }
    for (double value : {0.0, -0.0, 1.25, -1.75, 1e-200, 1e200,
            std::numeric_limits<double>::denorm_min(),
            std::numeric_limits<double>::infinity(),
            -std::numeric_limits<double>::infinity()}) {
        const long double extended = value;
        memcpy(memory+32, &extended, 10);
        const double restored = FPU_FLD80(32, fpu.regs_80[TOP]);
        assert(restored == value && std::signbit(restored) == std::signbit(value));
    }
    const long double nan_value = std::numeric_limits<long double>::quiet_NaN();
    memcpy(memory+32, &nan_value, 10);
    assert(std::isnan(FPU_FLD80(32, fpu.regs_80[TOP])));
    for (double value : {1.25, 1.75, -1.25, -1.75, 9223372036854775808.0}) {
        const long double extended = value;
        memcpy(memory+32, &extended, 10);
        fpu.regs[TOP].d = FPU_FLD80(32, fpu.regs_80[TOP]);
        fpu.use80[TOP] = true;
        FPU_FST_I64(16);
        int64_t got; memcpy(&got, memory+16, 8);
        assert(got == (value < 9223372036854775808.0 ? (int64_t)std::nearbyint(value) : INT64_MIN));
        FPU_FSTT_I64(16); memcpy(&got, memory+16, 8);
        assert(got == (value < 9223372036854775808.0 ? (int64_t)value : INT64_MIN));
    }
    puts("PASS: 10015 exact integer direct/extended round-trips, double and arithmetic continuity, fractional stores");
}
'''
with tempfile.TemporaryDirectory(prefix='fpu-i64-') as tmp:
    cpp = Path(tmp) / 'test.cpp'
    cpp.write_text('#include <initializer_list>\n' + fixture + '\n'.join(functions) + tests)
    exe = Path(tmp) / 'test'
    subprocess.run(['g++', '-std=c++11', '-O2', '-fsanitize=undefined', '-fno-sanitize-recover=all', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)

/*
 * WASM-oriented DOSBox-X CPU core for js-dos / Emscripten builds.
 *
 * This is deliberately not a dynamic/JIT core.  It is a normal-core compatible
 * interpreter with a small sequential instruction prefetch cache, branch-driven
 * cache invalidation, and aggressive inlining for WebAssembly builds.
 *
 * Install with tools/add_wasm_core.py from the repository root.
 */

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "cpu.h"
#include "lazyflags.h"
#include "callback.h"
#include "paging.h"
#include "pic.h"
#include "fpu.h"
#include "mmx.h"

extern bool do_lds_wraparound;
bool CPU_RDMSR();
bool CPU_WRMSR();
bool CPU_SYSENTER();
bool CPU_SYSEXIT();

#define PRE_EXCEPTION { }
#define CPU_CORE CPU_ARCHTYPE_386
#define DoString DoString_Prefetch

static uint16_t last_ea86_offset;
extern bool ignore_opcode_63;

#if C_DEBUG
#include "debug.h"
#endif

#define LoadMb(off) mem_readb_inline(off)
#define LoadMw(off) mem_readw_inline(off)
#define LoadMd(off) mem_readd_inline(off)
#define LoadMq(off) ((uint64_t)((uint64_t)mem_readd_inline((off)+4) << 32 | (uint64_t)mem_readd_inline(off)))
#define SaveMb(off,val) mem_writeb_inline(off,val)
#define SaveMw(off,val) mem_writew_inline(off,val)
#define SaveMd(off,val) mem_writed_inline(off,val)
#define SaveMq(off,val) { mem_writed_inline((off),(val)&0xffffffffu); mem_writed_inline((off)+4,((val)>>32)&0xffffffffu); }

extern Bitu cycle_count;

#if C_FPU
#define CPU_FPU 1u
#endif
#define CPU_PIC_CHECK 1u
#define CPU_TRAP_CHECK 1u
#define CPU_TRAP_DECODER CPU_Core_Wasm_Trap_Run

#define OPCODE_NONE 0x000u
#define OPCODE_0F   0x100u
#define OPCODE_SIZE 0x200u

#define PREFIX_ADDR 0x1u
#define PREFIX_REP  0x2u

#define TEST_PREFIX_ADDR (core.prefixes & PREFIX_ADDR)
#define TEST_PREFIX_REP  (core.prefixes & PREFIX_REP)

#define DO_PREFIX_SEG(_SEG) \
    BaseDS = SegBase(_SEG); \
    BaseSS = SegBase(_SEG); \
    core.base_val_ds = _SEG; \
    goto restart_opcode;

#define DO_PREFIX_ADDR() \
    core.prefixes = (core.prefixes & ~PREFIX_ADDR) | (cpu.code.big ^ PREFIX_ADDR); \
    core.ea_table = &EATable[(core.prefixes & 1u) * 256u]; \
    goto restart_opcode;

#define DO_PREFIX_REP(_ZERO) \
    core.prefixes |= PREFIX_REP; \
    core.rep_zero = _ZERO; \
    goto restart_opcode;

#define REMEMBER_PREFIX(_x) last_prefix = (_x)
static uint8_t last_prefix;

typedef PhysPt (*GetEAHandler)(void);

static const uint32_t AddrMaskTable[2] = {0x0000ffffu, 0xffffffffu};

static struct {
    Bitu opcode_index;
    PhysPt cseip;
    PhysPt base_ds, base_ss;
    SegNames base_val_ds;
    bool rep_zero;
    Bitu prefixes;
    GetEAHandler *ea_table;
} core;

#define GETIP  ((PhysPt)(core.cseip - SegBase(cs)))
#define SAVEIP reg_eip = GETIP;
#define LOADIP core.cseip = ((PhysPt)(SegBase(cs) + reg_eip));

#define SegBase(c) SegPhys(c)
#define BaseDS core.base_ds
#define BaseSS core.base_ss

#if defined(__clang__) || defined(__GNUC__)
#define WASM_ALWAYS_INLINE inline __attribute__((always_inline))
#define WASM_HOT __attribute__((hot))
#define WASM_UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define WASM_ALWAYS_INLINE inline
#define WASM_HOT
#define WASM_UNLIKELY(x) (x)
#endif

/*
 * Larger than the historical 386/486 queue on purpose: this is a browser-speed
 * fetch cache, not a cycle-accurate hardware prefetch model.  Branches and
 * other control-flow opcodes invalidate it before the next instruction.
 */
static constexpr Bitu WASM_PQ_SIZE = 64;
static constexpr Bitu WASM_PQ_REFILL = 16;
static uint8_t wasm_pq[WASM_PQ_SIZE];
static bool pq_valid = false;
static PhysPt pq_start = 0;
static PhysPt pq_fill = 0;
static Bitu pq_limit = WASM_PQ_SIZE;

static WASM_ALWAYS_INLINE PhysPt wasm_align_fetch(const PhysPt addr)
{
    return addr & ~(PhysPt)3u;
}

template <typename T>
static WASM_ALWAYS_INLINE bool wasm_pq_hit(const PhysPt addr)
{
    return pq_valid && addr >= pq_start && (addr + sizeof(T)) <= pq_fill;
}

template <typename T>
static WASM_ALWAYS_INLINE T wasm_pq_read(const PhysPt addr);

template <>
WASM_ALWAYS_INLINE uint8_t wasm_pq_read<uint8_t>(const PhysPt addr)
{
    return wasm_pq[addr - pq_start];
}

template <>
WASM_ALWAYS_INLINE uint16_t wasm_pq_read<uint16_t>(const PhysPt addr)
{
    return host_readw(&wasm_pq[addr - pq_start]);
}

template <>
WASM_ALWAYS_INLINE uint32_t wasm_pq_read<uint32_t>(const PhysPt addr)
{
    return host_readd(&wasm_pq[addr - pq_start]);
}

static WASM_ALWAYS_INLINE void wasm_pq_fill_dword()
{
    host_writed(&wasm_pq[pq_fill - pq_start], LoadMd(pq_fill));
    pq_fill += 4;
}

static WASM_ALWAYS_INLINE void wasm_pq_refill_until(const PhysPt stop)
{
    const PhysPt hard_stop = pq_start + pq_limit;
    const PhysPt wanted = std::min(stop, hard_stop);
    while (pq_fill < wanted)
        wasm_pq_fill_dword();
}

static WASM_ALWAYS_INLINE void wasm_pq_init(const PhysPt addr)
{
    pq_start = wasm_align_fetch(addr);
    pq_fill = pq_start;
    pq_valid = true;
    wasm_pq_refill_until(addr + WASM_PQ_REFILL);
}

static WASM_ALWAYS_INLINE void wasm_pq_soft_slide_or_invalidate(const PhysPt next)
{
    /* Avoid memmove in the hot path.  Once the cursor is in the second half,
     * force a cheap refill on the next fetch instead of sliding the cache.
     */
    if (WASM_UNLIKELY((next - pq_start) >= (WASM_PQ_SIZE / 2)))
        pq_valid = false;
}

template <typename T>
static WASM_ALWAYS_INLINE T WasmFetch()
{
    const PhysPt addr = core.cseip;
    if (WASM_UNLIKELY(!wasm_pq_hit<T>(addr)))
        wasm_pq_init(addr);

    wasm_pq_refill_until(addr + sizeof(T));
    const T value = wasm_pq_read<T>(addr);
    core.cseip += sizeof(T);

    wasm_pq_refill_until(core.cseip + WASM_PQ_REFILL);
    wasm_pq_soft_slide_or_invalidate(core.cseip);
    return value;
}

template <typename T>
static WASM_ALWAYS_INLINE T WasmFetchPeek()
{
    const PhysPt addr = core.cseip;
    if (WASM_UNLIKELY(!wasm_pq_hit<T>(addr)))
        wasm_pq_init(addr);
    wasm_pq_refill_until(addr + sizeof(T));
    return wasm_pq_read<T>(addr);
}

template <typename T>
static WASM_ALWAYS_INLINE void WasmFetchDiscard()
{
    core.cseip += sizeof(T);
    wasm_pq_soft_slide_or_invalidate(core.cseip);
}

static WASM_ALWAYS_INLINE void FetchDiscardb()
{
    WasmFetchDiscard<uint8_t>();
}

static WASM_ALWAYS_INLINE uint8_t FetchPeekb()
{
    return WasmFetchPeek<uint8_t>();
}

static WASM_ALWAYS_INLINE uint8_t Fetchb()
{
    return WasmFetch<uint8_t>();
}

static WASM_ALWAYS_INLINE uint16_t Fetchw()
{
    return WasmFetch<uint16_t>();
}

static WASM_ALWAYS_INLINE uint32_t Fetchd()
{
    return WasmFetch<uint32_t>();
}

static WASM_ALWAYS_INLINE bool wasm_opcode_invalidates_prefetch(const Bitu opcode_index,
                                                                const uint8_t opcode)
{
    if (opcode_index & OPCODE_0F)
        return true;

    switch (opcode) {
    case 0x70: case 0x71: case 0x72: case 0x73:
    case 0x74: case 0x75: case 0x76: case 0x77:
    case 0x78: case 0x79: case 0x7a: case 0x7b:
    case 0x7c: case 0x7d: case 0x7e: case 0x7f:
    case 0x9a:
    case 0xc2: case 0xc3: case 0xc8: case 0xc9:
    case 0xca: case 0xcb: case 0xcc: case 0xcd:
    case 0xce: case 0xcf:
    case 0xe0: case 0xe1: case 0xe2: case 0xe3:
    case 0xe8: case 0xe9: case 0xea: case 0xeb:
    case 0xff:
        return true;
    default:
        return false;
    }
}

#define Push_16 CPU_Push16
#define Push_32 CPU_Push32
#define Pop_16 CPU_Pop16
#define Pop_32 CPU_Pop32

#include "instructions.h"
#include "core_normal/support.h"
#include "core_normal/string.h"

#define EALookupTable (core.ea_table)

void CPU_Core_Wasm_Reset(void)
{
    pq_valid = false;
    pq_start = pq_fill = 0;
}

Bits WASM_HOT CPU_Core_Wasm_Run(void)
{
    bool invalidate_pq = false;

    if (CPU_Cycles <= 0)
        return CBRET_NONE;

    pq_limit = WASM_PQ_SIZE;

    while (CPU_Cycles-- > 0) {
        if (WASM_UNLIKELY(invalidate_pq)) {
            pq_valid = false;
            invalidate_pq = false;
        }

        LOADIP;
        last_prefix = MP_NONE;
        core.opcode_index = cpu.code.big * (Bitu)0x200u;
        core.prefixes = cpu.code.big;
        last_ea86_offset = 0;
        core.ea_table = &EATable[cpu.code.big * 256u];
        BaseDS = SegBase(ds);
        BaseSS = SegBase(ss);
        core.base_val_ds = ds;

#if C_DEBUG
#if C_HEAVY_DEBUG
        if (DEBUG_HeavyIsBreakpoint()) {
            FillFlags();
            return (Bits)debugCallback;
        }
#endif
#endif

        cycle_count++;

restart_opcode:
        {
            const uint8_t next_opcode = Fetchb();
            invalidate_pq = wasm_opcode_invalidates_prefetch(core.opcode_index, next_opcode);

            switch (core.opcode_index + next_opcode) {
#include "core_normal/prefix_none.h"
#include "core_normal/prefix_0f.h"
#include "core_normal/prefix_66.h"
#include "core_normal/prefix_66_0f.h"
            default:
illegal_opcode:
#if C_DEBUG
                {
                    bool ignore = false;
                    Bitu len = (GETIP - reg_eip);
                    LOADIP;
                    if (len > 16)
                        len = 16;
                    char tempcode[16 * 2 + 1];
                    char *writecode = tempcode;
                    if (ignore_opcode_63 && mem_readb(core.cseip) == 0x63)
                        ignore = true;
                    for (; len > 0; len--) {
                        std::sprintf(writecode, "%02X", mem_readb(core.cseip++));
                        writecode += 2;
                    }
                    if (!ignore)
                        LOG(LOG_CPU, LOG_NORMAL)("Illegal/Unhandled opcode %s", tempcode);
                }
#endif
                CPU_Exception(6, 0);
                invalidate_pq = true;
                continue;

gp_fault:
                LOG_MSG("Segment limit violation");
                CPU_Exception(EXCEPTION_GP, 0);
                invalidate_pq = true;
                continue;
            }
        }

        SAVEIP;
    }

    FillFlags();
    return CBRET_NONE;

decode_end:
    SAVEIP;
    FillFlags();
    return CBRET_NONE;
}

Bits CPU_Core_Wasm_Trap_Run(void)
{
    const Bits oldCycles = CPU_Cycles;
    CPU_Cycles = 1;
    cpu.trap_skip = false;

    const Bits ret = CPU_Core_Wasm_Run();
    if (!cpu.trap_skip)
        CPU_DebugException(DBINT_STEP, reg_eip);

    CPU_Cycles = oldCycles - 1;
    cpudecoder = &CPU_Core_Wasm_Run;
    return ret;
}

void CPU_Core_Wasm_Init(void)
{
    CPU_Core_Wasm_Reset();
}

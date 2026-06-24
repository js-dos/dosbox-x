#!/usr/bin/env python3
"""
Install core=wasm into a DOSBox-X source tree.

This v4 installer is intentionally regex-based because DOSBox-X master has
changed formatting around CPU registration and selection blocks. It is
idempotent, creates .bak files before editing, and treats help-text changes as
optional so source-tree wording changes do not abort installation.

Run from DOSBox-X repository root:
    python tools/add_wasm_core.py

or from the unpacked kit:
    cd /path/to/dosbox-x
    python /path/to/jsdos-wasm-core-kit-v4/tools/add_wasm_core.py
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path.cwd().resolve()
THIS = Path(__file__).resolve()
KIT_ROOT = THIS.parents[1]


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return False


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8", newline="")


def require(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run this from the DOSBox-X repository root.")


def sub_once(text: str, pattern: str, repl: str | callable, label: str, flags: int = re.M | re.S) -> str:
    new_text, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        raise RuntimeError(f"Could not find insertion point for {label!r}")
    return new_text


def install_core_file() -> None:
    dst = ROOT / "src" / "cpu" / "core_wasm.cpp"
    candidates = [
        KIT_ROOT / "src" / "cpu" / "core_wasm.cpp",
        THIS.parent / "core_wasm.cpp",
        ROOT / "jsdos-wasm-core-kit" / "src" / "cpu" / "core_wasm.cpp",
        ROOT / "jsdos-wasm-core-kit-v4" / "src" / "cpu" / "core_wasm.cpp",
        ROOT.parent / "jsdos-wasm-core-kit" / "src" / "cpu" / "core_wasm.cpp",
        ROOT.parent / "jsdos-wasm-core-kit-v4" / "src" / "cpu" / "core_wasm.cpp",
    ]
    src = next((p for p in candidates if p.exists()), None)

    if src is None:
        if dst.exists():
            print("core_wasm.cpp already exists in src/cpu; skipping copy.")
            return
        raise SystemExit("Could not find core_wasm.cpp. Copy it to src/cpu/core_wasm.cpp first.")

    if same_file(src, dst):
        print("core_wasm.cpp source and destination are the same file; skipping copy.")
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        backup = dst.with_suffix(dst.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(dst, backup)
    shutil.copy2(src, dst)
    print("installed src/cpu/core_wasm.cpp")


def patch_cpu_h() -> None:
    path = ROOT / "include" / "cpu.h"
    text = read(path)

    if "CPU_Core_Wasm_Run" not in text:
        text = sub_once(
            text,
            r"(Bits\s+CPU_Core_Prefetch_Trap_Run\s*\(\s*void\s*\)\s*;)",
            r"\1\nBits CPU_Core_Wasm_Run(void);\nBits CPU_Core_Wasm_Trap_Run(void);",
            "cpu.h wasm decoder declarations",
        )
        print("patched include/cpu.h")
    else:
        print("include/cpu.h already patched")

    write(path, text)


def patch_makefile_am() -> None:
    path = ROOT / "src" / "cpu" / "Makefile.am"
    text = read(path)

    if "core_wasm.cpp" not in text:
        # Keep this deliberately simple: Automake accepts multiple sources on one continued line.
        text = sub_once(
            text,
            r"core_prefetch\.cpp",
            "core_prefetch.cpp core_wasm.cpp",
            "src/cpu/Makefile.am source list",
            flags=re.M,
        )
        print("patched src/cpu/Makefile.am")
    else:
        print("src/cpu/Makefile.am already patched")

    write(path, text)


def patch_dosbox_cpp() -> None:
    path = ROOT / "src" / "dosbox.cpp"
    text = read(path)

    if not re.search(r"const\s+char\s*\*\s*cores\s*\[\s*\]\s*=\s*\{[^}]*\"wasm\"", text, re.S):
        text = sub_once(
            text,
            r"(const\s+char\s*\*\s*cores\s*\[\s*\]\s*=\s*\{.*?)(\"normal\")",
            r"\1#if defined(C_EMSCRIPTEN)\n\"wasm\",\n#endif\n\2",
            "dosbox.cpp cores[] wasm value",
        )
        print("patched src/dosbox.cpp cores[]")
    else:
        print("src/dosbox.cpp cores[] already patched")

    if "wasm core is a WebAssembly-oriented" not in text:
        # DOSBox-X master changes the exact help wording fairly often. This is
        # documentation only; core registration is handled by cores[]. Try a
        # conservative patch, but never abort if the help text is different.
        patterns = [
            r'(Pstring->Set_help\(\"CPU Core used in emulation\.\\n\".*?dynamic_rec core\.)(\"\s*\);)',
            r'(Pstring->Set_help\(\"CPU Core used in emulation\.\\n\".*?)(\"\s*\);)',
        ]
        patched_help = False
        for pat in patterns:
            new_text, n = re.subn(
                pat,
                lambda m: (
                    m.group(1)
                    + '\\n"\n"wasm core is a WebAssembly-oriented normal/prefetch interpreter for Emscripten builds.'
                    + m.group(2)
                ),
                text,
                count=1,
                flags=re.M | re.S,
            )
            if n == 1:
                text = new_text
                patched_help = True
                print("patched src/dosbox.cpp help text")
                break
        if not patched_help:
            print("src/dosbox.cpp help text not found; skipping optional help-text patch")
    else:
        print("src/dosbox.cpp help text already patched")

    write(path, text)


def patch_cpu_cpp() -> None:
    path = ROOT / "src" / "cpu" / "cpu.cpp"
    text = read(path)

    if "void CPU_Core_Wasm_Init(void);" not in text:
        text = sub_once(
            text,
            r"(void\s+CPU_Core_Normal_Init\s*\(\s*void\s*\)\s*;)",
            r"\1\n#if defined(C_EMSCRIPTEN)\nvoid CPU_Core_Wasm_Init(void);\n#endif",
            "cpu.cpp wasm init declaration",
        )
        print("patched cpu.cpp wasm init declaration")
    else:
        print("cpu.cpp wasm init declaration already patched")

    if "CPU_Core_Wasm_Init();" not in text:
        # There is one constructor-time init call; patch the first normal-core init call only.
        text = sub_once(
            text,
            r"(CPU_Core_Normal_Init\s*\(\s*\)\s*;)",
            r"\1\n#if defined(C_EMSCRIPTEN)\n    CPU_Core_Wasm_Init();\n#endif",
            "cpu.cpp wasm init call",
        )
        print("patched cpu.cpp wasm init call")
    else:
        print("cpu.cpp wasm init call already patched")

    if 'core == "wasm"' not in text:
        # Patch the Change_Config core selector. This is the block that currently falls through:
        #   if (core == "normal") { ... } else if (core =="simple") { ... }
        pattern = (
            r'if\s*\(\s*core\s*==\s*"normal"\s*\)\s*\{\s*'
            r'cpudecoder\s*=\s*&CPU_Core_Normal_Run\s*;\s*'
            r'\}\s*else\s*if\s*\(\s*core\s*==\s*"simple"\s*\)\s*\{'
        )
        replacement = (
            'if (core == "normal") {\n'
            '        cpudecoder=&CPU_Core_Normal_Run;\n'
            '#if defined(C_EMSCRIPTEN)\n'
            '    } else if (core == "wasm") {\n'
            '        cpudecoder=&CPU_Core_Wasm_Run;\n'
            '        CPU_PrefetchQueueSize = 64;\n'
            '#endif\n'
            '    } else if (core =="simple") {'
        )
        text = sub_once(text, pattern, replacement, "cpu.cpp core=wasm selection")
        print("patched cpu.cpp core=wasm selection")
    else:
        print("cpu.cpp core=wasm selection already patched")

    if "CPU_ToggleWasmCore" not in text:
        text = sub_once(
            text,
            r"(static\s+void\s+CPU_ToggleNormalCore\s*\([^)]*\)\s*\{.*?\n\s*\})",
            r"\1\n#if defined(C_EMSCRIPTEN)\nstatic void CPU_ToggleWasmCore(bool pressed) {\n    if (!pressed) return;\n    Section* sec=control->GetSection(\"cpu\");\n    if(sec) {\n        std::string tmp=\"core=wasm\";\n        sec->HandleInputline(tmp);\n    }\n}\n#endif",
            "cpu.cpp wasm mapper function",
        )
        print("patched cpu.cpp wasm mapper function")
    else:
        print("cpu.cpp wasm mapper function already patched")

    if '"wasm" ,"CPU: wasm core"' not in text and '"wasm","CPU: wasm core"' not in text:
        text = sub_once(
            text,
            r'(MAPPER_AddHandler\s*\(\s*CPU_ToggleNormalCore\s*,.*?item->set_text\s*\(\s*"Normal core"\s*\)\s*;)',
            r"\1\n#if defined(C_EMSCRIPTEN)\n        MAPPER_AddHandler(CPU_ToggleWasmCore,MK_nothing,0,\"wasm\",\"CPU: wasm core\",&item);\n        item->set_text(\"WASM core\");\n#endif",
            "cpu.cpp wasm mapper handler",
        )
        print("patched cpu.cpp wasm mapper handler")
    else:
        print("cpu.cpp wasm mapper handler already patched")

    if "CPU_Core_Wasm_Run ) decoder_idx" not in text and "CPU_Core_Wasm_Run) decoder_idx" not in text:
        text = sub_once(
            text,
            r"(else\s+if\s*\(\s*cpudecoder\s*==\s*&CPU_Core_Prefetch_Run\s*\)\s*decoder_idx\s*=\s*1\s*;)",
            r"\1\n#if defined(C_EMSCRIPTEN)\n    else if( cpudecoder == &CPU_Core_Wasm_Run ) decoder_idx = 6;\n#endif",
            "cpu.cpp savestate wasm decoder id",
        )
        print("patched cpu.cpp savestate decoder id")
    else:
        print("cpu.cpp savestate decoder id already patched")

    if "case 6: return &CPU_Core_Wasm_Run" not in text:
        text = sub_once(
            text,
            r"(case\s+1\s*:\s*return\s*&CPU_Core_Prefetch_Run\s*;)",
            r"\1\n#if defined(C_EMSCRIPTEN)\n        case 6: return &CPU_Core_Wasm_Run;\n#endif",
            "cpu.cpp savestate wasm decoder lookup",
        )
        print("patched cpu.cpp savestate decoder lookup")
    else:
        print("cpu.cpp savestate decoder lookup already patched")

    write(path, text)


def main() -> None:
    for path in [
        ROOT / "src" / "cpu" / "cpu.cpp",
        ROOT / "include" / "cpu.h",
        ROOT / "src" / "dosbox.cpp",
        ROOT / "src" / "cpu" / "Makefile.am",
    ]:
        require(path)

    install_core_file()
    patch_cpu_h()
    patch_makefile_am()
    patch_dosbox_cpp()
    patch_cpu_cpp()
    print("Installed core=wasm patch. Now run: ./autogen.sh && ./build-emscripten-sdl2")


if __name__ == "__main__":
    main()

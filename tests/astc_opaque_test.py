"""Builds and runs tests/astc_opaque_test.c: the opaque-block classification of runtime/astc_opaque.h.

Pure logic, no Vulkan and no game data. Also checks that the pieces that use it are in place: the BC1 shader, its
header's source hash, the encoder's BC1 mode, and the upload's choice and the write path in vk_texture.c.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cc', default='clang')
    ap.add_argument('--opt', default='1')
    args = ap.parse_args()
    shader = ROOT / 'runtime/shaders/bc1_encode.comp'
    header = (ROOT / 'runtime/bc1_encode_spv.h').read_text(encoding='utf-8')
    digest = hashlib.sha256(shader.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if f'// source sha256 {digest}' not in header:
        raise SystemExit('runtime/bc1_encode_spv.h does not match runtime/shaders/bc1_encode.comp')
    texture = (ROOT / 'runtime/vk_texture.c').read_text(encoding='utf-8')
    astc = (ROOT / 'runtime/vk_astc.c').read_text(encoding='utf-8')
    for needle in ('astc_opaque_scan(&sc, tmp, raw, 1)', 'astc_bc_choice(was, &sc)', 'astc_bc_settled(was, choice',
                   'vk_astc_bc_remake(', 'vk_astc_bc1()', 'vk_texture_astc_bc3(&e->pub)'):
        if needle not in texture:
            raise SystemExit(f'vk_texture.c: missing {needle!r}')
    if 'bc == 2 ? bc1_pipe : bc_pipe' not in astc:
        raise SystemExit('vk_astc.c: the BC1 pipeline is not bound for bc 2')
    with tempfile.TemporaryDirectory(prefix='astc-opaque-') as tmp:
        exe = Path(tmp) / 'astc_opaque_test'
        subprocess.run([args.cc, f'-O{args.opt}', '-Wall', '-Werror', '-I', str(ROOT / 'runtime'),
                        str(ROOT / 'tests/astc_opaque_test.c'), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    main()

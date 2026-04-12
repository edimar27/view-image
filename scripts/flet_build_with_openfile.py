#!/usr/bin/env python3
"""
Executa «flet build» com um patch ao main.dart gerado: o template Flet assume
que qualquer argv no desktop é modo dev (URL). «Abrir com» no macOS passa o
caminho do ficheiro — sem o patch a app não corre o Python corretamente.

Uso (equivalente a «flet build …»):
  python scripts/flet_build_with_openfile.py build macos --yes
"""
from __future__ import annotations

import sys
from pathlib import Path


def patch_main_dart_for_openfile(flutter_dir: Path) -> None:
    main_dart = flutter_dir / "lib" / "main.dart"
    if not main_dart.is_file():
        return
    text = main_dart.read_text(encoding="utf-8")
    if "_isDeveloperModeArgs" in text:
        return

    old_decl = 'String pageUrl = "";\nString assetsDir = "";'
    if old_decl not in text:
        raise RuntimeError(
            "Ed Image Preview: patch «Abrir com» — main.dart do Flet não reconhecido. "
            "Atualize scripts/flet_build_with_openfile.py para a sua versão do Flet."
        )

    replacement = '''String pageUrl = "";

bool _isDeveloperModeArgs(List<String> args) {
  if (args.isEmpty) return false;
  final a = args.first;
  return a.startsWith('http://') ||
      a.startsWith('https://') ||
      a.startsWith('tcp://') ||
      a.startsWith('flet_');
}

String assetsDir = "";'''

    text = text.replace(old_decl, replacement, 1)
    text = text.replace(
        "return kIsWeb || (isDesktopPlatform() && _args.isNotEmpty)",
        "return kIsWeb || (isDesktopPlatform() && _isDeveloperModeArgs(_args))",
        1,
    )
    text = text.replace(
        "} else if (_args.isNotEmpty && isDesktopPlatform()) {",
        "} else if (_isDeveloperModeArgs(_args) && isDesktopPlatform()) {",
        1,
    )
    main_dart.write_text(text, encoding="utf-8")


def main() -> None:
    import flet_cli.commands.build_base as build_base

    _orig = build_base.BaseBuildCommand.run_flutter

    def _run_flutter_patched(self: object) -> None:
        fd = getattr(self, "flutter_dir", None)
        if fd is not None:
            patch_main_dart_for_openfile(Path(fd))
        _orig(self)

    build_base.BaseBuildCommand.run_flutter = _run_flutter_patched  # type: ignore[method-assign]

    import flet_cli.cli as cli

    sys.argv = ["flet", *sys.argv[1:]]
    cli.main()


if __name__ == "__main__":
    main()

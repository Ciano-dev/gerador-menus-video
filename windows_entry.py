"""Entrypoint pequeno; instalar hooks antes de importar Tk/aplicação."""

import argparse
from pathlib import Path
import sys

from diagnosticos import Diagnostics


def main(argv=None):
    parser = argparse.ArgumentParser(description='Gerador de menu não interativo')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--smoke-download', action='store_true',
                        help='Autorizar download somente no smoke automatizado explícito.')
    parser.add_argument('--smoke-scale-seconds', type=int, choices=(15, 30),
                        help='Escala real do smoke: 15s na primeira abertura, 30s na segunda.')
    args = parser.parse_args(argv)
    if args.smoke_scale_seconds is not None and not args.smoke_test:
        parser.error('--smoke-scale-seconds exige --smoke-test')
    if args.smoke_download and not args.smoke_test:
        parser.error('--smoke-download exige --smoke-test')
    diagnostics = Diagnostics()
    diagnostics.install()
    try:
        if args.smoke_test:
            if args.report is None:
                raise ValueError('--smoke-test exige --report caminho')
            from smoke_windows import run_smoke
            return run_smoke(args.report, diagnostics, smoke_download=args.smoke_download,
                             scale_seconds=args.smoke_scale_seconds)
        if getattr(sys, 'frozen', False):
            from bootstrap_gui import prepare_tools
            if prepare_tools(diagnostics) is None:
                return 1 if diagnostics.failures else 0
        import menu_gui
        return menu_gui.run_app(diagnostics)
    except Exception:
        diagnostics.report('importação/inicialização', *sys.exc_info())
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

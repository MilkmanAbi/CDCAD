"""Shared options for commands exporting one model."""


def add_output(parser):
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--force", action="store_true", help="Replace an existing output")
    parser.add_argument("--allow-open", action="store_true", help="Permit export without positive-volume validation")


def add_units(parser):
    from .._common import UNITS

    parser.add_argument("--units", choices=UNITS, default="mm", help="Input dimensions (default: mm)")

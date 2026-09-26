"""Print the usage guide, or the README: `python -m plotsmith [readme]`.

The guide is the page to read before writing code with plotsmith; the README,
carried in the installed package's metadata, gives the reasons behind it.
Both come from the installed version, so they match the code a project runs.
"""

import sys
from importlib.metadata import metadata
from importlib.resources import files


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args:
        text = files('plotsmith').joinpath('guide.md').read_text(encoding='utf-8')
    elif args == ['readme']:
        text = metadata('plotsmith').get_payload()
        if not text:
            sys.exit('plotsmith: this install carries no README; upgrade it with '
                     '`uv lock --upgrade-package plotsmith`')
    else:
        sys.exit('usage: python -m plotsmith [readme]')
    # Bytes, so a Windows console or pipe in a legacy code page cannot choke.
    sys.stdout.buffer.write(text.encode('utf-8'))
    sys.stdout.flush()


if __name__ == '__main__':
    main()

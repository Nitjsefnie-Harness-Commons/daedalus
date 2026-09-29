"""The receiver-resolution rows: what the unplaced arm is PROVED is not.

`tests/_bound_site_rows.py` holds the red-exercise for the launch
analyser's own structured sink, and this holds the block that judges a
RECEIVER rather than a launch: the attribute chain spelled from a dotted
import of a standard-library root, the roots that reach a launch anyway,
and the names that stop the chain from being a proof at all.

The rows live apart because the table they came from is at its size
ceiling, and a control that cannot be added is a defect left unfixed.

Each row here is (label, source, expected sites), the shape
`BOUND_SITE_ROWS` is built from, so every consumer of that tuple sees
them unchanged.
"""

CHAIN_LIMB_ROWS = (
    # An attribute chain is the one receiver shape no bare-Name limb can
    # judge, and a member of a standard-library module is the
    # interpreter's own code rather than this repository's launch. The
    # positive row is the extension; the four after it are the roots that
    # reach a launch anyway, and the last two are the boundaries — a
    # repository module reached the same way, and a bare Name the limb
    # never sees. Every root in the set is a package, so each row here is
    # a source that could execute; `shutil` and `pty` are not, and the
    # set says so rather than carrying rows nothing can be.
    ('a-stdlib-dotted-import-member-is-a-proved-fixed-value',
     "import http.client\n"
     "http.client.HTTPConnection(timeout=30)\n",
     []),
    ('excluded-stdlib-root-asyncio-is-refused',
     "import asyncio.subprocess\n"
     "asyncio.subprocess.run(argv, timeout=30)\n",
     [(2, 'unreadable', 'unplaced')]),
    ('excluded-stdlib-root-concurrent-is-refused',
     "import concurrent.futures\n"
     "concurrent.futures.wait(futures, timeout=30)\n",
     [(2, 'unreadable', 'unplaced')]),
    ('excluded-stdlib-root-multiprocessing-is-refused',
     "import multiprocessing.connection\n"
     "multiprocessing.connection.wait(children, timeout=30)\n",
     [(2, 'unreadable', 'unplaced')]),
    ('excluded-stdlib-root-os-is-refused',
     "import os.path\n"
     "os.path.commonpath(paths, timeout=30)\n",
     [(2, 'unreadable', 'unplaced')]),
    ('a-repository-dotted-import-is-not-a-stdlib-root',
     "import daedalus_bridge.config\n"
     "daedalus_bridge.config.startup_paths(timeout=30)\n",
     [(2, 'unreadable', 'unplaced')]),
    # The descent is what the predicate's own `while` says, and nothing
    # in the tree needs it: no receiver is more than one step from its
    # root, so `while` and `if` read the live tree alike. The row is here
    # so that is a measurement rather than an accident -- narrowing the
    # descent to one step turns exactly this row red and nothing else.
    ('a-two-step-dotted-stdlib-chain-is-proved',
     "import xml.etree.ElementTree\n"
     "xml.etree.ElementTree.parse(timeout=30)\n",
     []),
    ('a-parameter-shadowing-a-stdlib-root-is-refused',
     "import http.client\n"
     "def probe(http):\n"
     "    return http.client.HTTPConnection(timeout=30)\n",
     [(3, 'unreadable', 'unplaced')]),
    ('a-bare-name-receiver-is-unaffected-by-the-stdlib-limb',
     "import os\n"
     "os(timeout=30)\n",
     []),
    # The limb reads the root off the IMPORT's spelling, so a name the
    # module has since bound is not the one the import put there. Two
    # receiver shapes, because the bare name and the chain are the two it
    # proves; and the negative, because a name SOME OTHER binding took
    # must not cost an intact root its proof.
    ('a-rebound-dotted-root-is-not-proved',
     "import subprocess\n"
     "import http.client\n"
     "http = getattr(cfg, 'client')\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "http.request(['git', 'status'], timeout=30)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('a-rebound-dotted-root-blocks-the-chain-limb',
     "import subprocess\n"
     "import email.mime.text\n"
     "email = getattr(cfg, 'mail')\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "email.mime.text.send(['git', 'status'], timeout=30)\n",
     [(5, 'unreadable', 'unplaced')]),
    ('another-bindings-name-leaves-the-dotted-root-proved',
     "import subprocess\n"
     "import http.client\n"
     "other = getattr(cfg, 'x')\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "http.client.HTTPSConnection('h', timeout=30)\n",
     []),
    # The set is consulted on the ROOT, so a member of an excluded root
    # named directly walks past it: the root is never a dotted import, so
    # the chain limb never sees it and the bare-Name limb proves the
    # member. The negative keeps the guard to the roots that are in the
    # set rather than to every from-import.
    ('a-from-import-of-an-excluded-root-member-is-refused',
     "import subprocess\n"
     "from multiprocessing.connection import wait\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "wait(children, timeout=30)\n",
     [(4, 'unreadable', 'unplaced')]),
    ('a-from-import-of-an-unexcluded-root-is-still-proved',
     "import subprocess\n"
     "from http.client import HTTPConnection\n"
     "subprocess.run(['git', 'status'], check=True)\n"
     "HTTPConnection('h', timeout=30)\n",
     []),
)

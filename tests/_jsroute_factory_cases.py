"""Factory and constructor sender-provenance controls."""


FACTORY_CASES = [
    ('assigned-factory-promotion',
     "function make(send) { return {run: send}; }\n"
     "const box = make(extCmd);\n"
     "box.run('focus-tab', { tab: chromeTab });\n", True),
    ('assigned-factory-demotion',
     "function make(send) { return {run: send}; }\n"
     "const box = make(ordinary);\n"
     "box.run('focus-tab', { tab: chromeTab });\n", False),
    ('chained-factory-promotion',
     "function make(send) { return {run: send}; }\n"
     "make(extCmd).run('focus-tab', { tab: chromeTab });\n", True),
    ('chained-factory-demotion',
     "function make(send) { return {run: send}; }\n"
     "make(ordinary).run('focus-tab', { tab: chromeTab });\n", False),
    ('concise-factory-promotion',
     "const make = send => ({run: send});\n"
     "const box = make(extCmd);\n"
     "box.run('focus-tab', { tab: chromeTab });\n", True),
    ('concise-factory-demotion',
     "const make = send => ({run: send});\n"
     "const box = make(ordinary);\n"
     "box.run('focus-tab', { tab: chromeTab });\n", False),
    ('constructor-member-promotion',
     "function Holder(send) { this.run = send; }\n"
     "const box = new Holder(extCmd);\n"
     "box.run('focus-tab', { tab: chromeTab });\n", True),
    ('constructor-member-demotion',
     "function Holder(send) { this.run = send; }\n"
     "const box = new Holder(ordinary);\n"
     "box.run('focus-tab', { tab: chromeTab });\n", False),
]

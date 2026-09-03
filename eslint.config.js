// Flat config for the pinned ESLint, replacing the retired .eslintrc.json
// format. The old file's semantics are carried over: the recommended rule
// set, module sources by default, the browser globals the dashboard and the
// extension assume, and the same per-path source types.
//
// There is still deliberately NO package.json: ESLint and the two helpers
// this config requires are pinned in .github/workflows/tests.yml and
// installed with --no-save, so the repository does not read as having
// adopted a build step it does not have.
const js = require('@eslint/js');
const globals = require('globals');

module.exports = [
    {
        // Snippets the bridge wraps in an async function, so they carry
        // top-level `return` and `await` together; no sourceType describes
        // that shape. The workflow's file list excludes them too.
        ignores: ['examples/'],
    },
    {
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: { ...globals.browser },
        },
        rules: {
            ...js.configs.recommended.rules,
            // caughtErrors: 'none' is eslint 8's default, which the previous
            // config inherited; eslint 9+ reports unused catch bindings.
            'no-unused-vars': ['error', {
                args: 'none',
                caughtErrors: 'none',
                varsIgnorePattern: '^_',
            }],
            'no-empty': ['error', { allowEmptyCatch: true }],
            'no-console': 'off',
            'no-constant-condition': ['error', { checkLoops: false }],
        },
    },
    {
        // node loads this file as CommonJS, and `git ls-files '*.js'` puts it
        // in the lint list with the sources it configures.
        files: ['eslint.config.js'],
        languageOptions: {
            sourceType: 'commonjs',
            globals: {
                require: 'readonly',
                module: 'writable',
            },
        },
    },
    {
        // Content and extension pages are classic scripts, and a top-level
        // `return` in them is the flat config's `commonjs` source type --
        // flat config rejects the old ecmaFeatures.globalReturn spelling.
        files: ['extension/*.js'],
        languageOptions: {
            sourceType: 'commonjs',
            globals: {
                chrome: 'readonly',
                GM: 'writable',
                GM_addStyle: 'readonly',
                GM_xmlhttpRequest: 'readonly',
                unsafeWindow: 'readonly',
            },
        },
    },
    {
        // The MV3 service worker runs as a worker global, not a page.
        files: ['extension/background.js'],
        languageOptions: {
            globals: { ...globals.serviceworker },
        },
    },
    {
        files: ['extension/worker/*.js'],
        languageOptions: {
            sourceType: 'script',
            globals: { ...globals.serviceworker, chrome: 'readonly' },
        },
    },
];

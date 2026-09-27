import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // '.vite' is Vite's dependency pre-bundle cache: machine-generated copies
  // of node_modules that account for three hundred of the errors this config
  // reported and none of the bugs. 'src/components/ui' is shadcn's, copied in
  // by its CLI and updated by re-running it — our lint rules are not the
  // right place to argue with it.
  globalIgnores(['dist', '.vite', 'src/components/ui']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
  },
])

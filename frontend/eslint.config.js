import js from '@eslint/js'
import vueParser from 'vue-eslint-parser'
import pluginVue from 'eslint-plugin-vue'

export default [
  {
    ignores: ['dist/**', 'coverage/**', 'node_modules/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['src/**/*.js', 'src/**/*.vue', 'vite.config.js'],
    languageOptions: {
      parser: vueParser,
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        console: 'readonly',
        document: 'readonly',
        DOMParser: 'readonly',
        EventSource: 'readonly',
        fetch: 'readonly',
        FormData: 'readonly',
        HTMLElement: 'readonly',
        location: 'readonly',
        navigator: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        URL: 'readonly',
        window: 'readonly',
      },
    },
    rules: {
      'no-console': 'off',
      'no-undef': 'error',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', ignoreRestSiblings: true }],
      'vue/html-self-closing': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/multi-word-component-names': 'off',
      'vue/singleline-html-element-content-newline': 'off',
    },
  },
]

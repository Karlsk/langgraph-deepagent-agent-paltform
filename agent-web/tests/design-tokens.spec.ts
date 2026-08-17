import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(
  fileURLToPath(new URL('../src/styles/index.css', import.meta.url)),
  'utf8',
)

describe('design tokens', () => {
  it('maps Element Plus color variants to semantic tokens', () => {
    for (const color of [
      { name: 'primary', token: '--color-primary-500' },
      { name: 'success', token: '--color-success-600' },
      { name: 'warning', token: '--color-warning-600' },
      { name: 'danger', token: '--color-danger-600' },
    ]) {
      for (const [shade, percentage] of [
        ['light-3', 70],
        ['light-5', 50],
        ['light-7', 30],
        ['light-8', 20],
        ['light-9', 10],
      ]) {
        expect(stylesheet).toContain(
          `--el-color-${color.name}-${shade}: color-mix(in srgb, var(${color.token}) ${percentage}%, var(--color-bg-surface));`,
        )
      }

      expect(stylesheet).toContain(
        `--el-color-${color.name}-dark-2: color-mix(in srgb, var(${color.token}) 80%, var(--color-text-primary));`,
      )
      expect(stylesheet).toContain(
        `--el-color-${color.name}-rgb: var(--color-${color.name}-rgb);`,
      )
    }
  })

  it('defines the approved A-palette semantic tokens', () => {
    expect(stylesheet).toContain('--color-primary-500: #635bff;')
    expect(stylesheet).toContain('--color-accent-500: #36d6b0;')
    expect(stylesheet).toContain('--color-bg-dark: #0b0f16;')
    expect(stylesheet).toContain('--color-bg-sidebar: var(--color-bg-dark);')
    expect(stylesheet).toContain('--color-bg-canvas: #f8f8fc;')
  })

  it('defines motion tokens and honors reduced-motion preferences', () => {
    expect(stylesheet).toContain('--duration-fast: 120ms;')
    expect(stylesheet).toContain('--ease-standard: cubic-bezier(.2, .8, .2, 1);')
    expect(stylesheet).toContain('@media (prefers-reduced-motion: reduce)')
  })

  it('uses semantic tokens for the application shell', () => {
    const shell = readFileSync(
      fileURLToPath(new URL('../src/App.vue', import.meta.url)),
      'utf8',
    )

    expect(shell).toContain('background: var(--color-bg-sidebar);')
    expect(shell).toContain('color: var(--color-text-on-dark);')
    expect(shell).toContain('background: var(--color-bg-canvas);')
  })

  it('keeps the light Hify glyph readable on the dark sidebar', () => {
    const shell = readFileSync(
      fileURLToPath(new URL('../src/App.vue', import.meta.url)),
      'utf8',
    )

    expect(shell).toMatch(
      /\.app-brand__mark\s*\{[\s\S]*?background: color-mix\(in srgb, var\(--color-primary-500\) 16%, transparent\);[\s\S]*?color: var\(--color-text-on-dark\);/,
    )
  })

  it('defines the Hify sidebar brand, navigation, and collapse tokens', () => {
    const shell = readFileSync(
      fileURLToPath(new URL('../src/App.vue', import.meta.url)),
      'utf8',
    )

    expect(shell).toContain('>Hify</strong>')
    expect(shell).toContain('<small>AI Agent Platform</small>')
    expect(shell).toContain('ChatDotRound')
    expect(shell).toContain('User')
    expect(shell).toContain('Setting')
    expect(shell).toContain('Fold')
    expect(shell).toContain('Expand')
    expect(shell).toContain(':aria-label="isSidebarCollapsed')
    expect(shell).toContain("'展开侧栏' : '折叠侧栏'")
    expect(shell).toContain('Version 0.1.0')

    expect(stylesheet).toContain(
      '--color-sidebar-hover: rgba(255, 255, 255, 0.1);',
    )
    expect(stylesheet).toContain(
      '--color-sidebar-active: rgba(255, 255, 255, 0.12);',
    )
    expect(stylesheet).toContain('--sidebar-indicator-width: 3px;')
    expect(stylesheet).toContain('--sidebar-width-expanded: 236px;')
    expect(stylesheet).toContain('--sidebar-width-collapsed: 72px;')
    expect(shell).toContain('background: var(--color-sidebar-hover);')
    expect(shell).toContain(
      'box-shadow: inset var(--sidebar-indicator-width) 0 0 var(--color-primary-500);',
    )
  })
})

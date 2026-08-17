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

    expect(shell).toContain('background: var(--color-bg-dark);')
    expect(shell).toContain('background: var(--color-bg-surface);')
    expect(shell).toContain('background: var(--color-bg-subtle);')
    expect(shell).toContain('border-bottom: 1px solid var(--color-border-default);')
    expect(shell).toContain('background: var(--color-bg-canvas);')
    expect(shell).toContain('color: var(--color-text-primary);')
  })

  it('defines the sidebar brand, navigation and footer', () => {
    const shell = readFileSync(
      fileURLToPath(new URL('../src/App.vue', import.meta.url)),
      'utf8',
    )

    expect(shell).toContain('>Agent Web</strong>')
    expect(shell).toContain('aria-hidden="true">A</span>')
    expect(shell).toContain('<small>AI Agent Platform</small>')
    expect(shell).toContain('mode="vertical"')
    expect(shell).toContain(':collapse="collapsed"')
    expect(shell).toContain('Fold')
    expect(shell).toContain('Expand')
    expect(shell).toContain('v0.1.0')
    expect(shell).toContain('ChatDotRound')
    expect(shell).toContain('User')
    expect(shell).toContain('Setting')
    expect(shell).toContain('el-breadcrumb')
    expect(shell).toContain('el-avatar')

    expect(stylesheet).toContain('--color-bg-dark: #0b0f16;')
    expect(stylesheet).toContain('--color-text-on-dark')
    expect(stylesheet).toContain('.content-card')
    expect(stylesheet).toContain('padding: 20px;')
    expect(stylesheet).toContain('border-radius: var(--radius-lg);')
    expect(stylesheet).toContain('@media (max-width: 768px)')
  })

  it('defines the top-bar action buttons and crumb bar', () => {
    const shell = readFileSync(
      fileURLToPath(new URL('../src/App.vue', import.meta.url)),
      'utf8',
    )

    expect(shell).toContain('FullScreen')
    expect(shell).toContain('Moon')
    expect(shell).toContain('app-header__icon-btn')
    expect(shell).toContain('app-crumb-bar')
    expect(shell).toContain('el-breadcrumb')
    expect(shell).toContain('el-avatar')
  })
})

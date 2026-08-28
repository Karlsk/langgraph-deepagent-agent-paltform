import { get, post } from '@/utils/request'

/** One entry in the catalog listing. */
export interface CatalogItem {
  name: string
  description?: string | null
}

/** Catalog of available entities grouped by type. */
export interface CatalogResponse {
  providers: CatalogItem[]
  skills: CatalogItem[]
  subagents: CatalogItem[]
  apps: CatalogItem[]
  mcps: CatalogItem[]
}

/** One entry in the import preview. */
export interface PreviewItem {
  name: string
  action: 'create' | 'skip'
  reason?: string | null
}

/** Preview of what the import would do. */
export interface PreviewResponse {
  providers: PreviewItem[]
  skills: PreviewItem[]
  subagents: PreviewItem[]
  apps: PreviewItem[]
  mcps: PreviewItem[]
}

/** Result for one entity after import. */
export interface ImportResultItem {
  name: string
  status: 'created' | 'skipped' | 'error'
  message?: string | null
}

/** Summary of import results grouped by entity type. */
export interface ImportResponse {
  providers: ImportResultItem[]
  skills: ImportResultItem[]
  subagents: ImportResultItem[]
  apps: ImportResultItem[]
  mcps: ImportResultItem[]
}

/** Selection type: '*' for all, or a list of names. */
export type EntitySelector = '*' | string[]

/**
 * GET /bundle/catalog
 * List available entities per type for the export UI.
 */
export function getBundleCatalog() {
  return get<CatalogResponse>('/bundle/catalog')
}

/**
 * POST /bundle/export
 * Export selected entities as a downloadable JSON bundle.
 * Returns a Blob for browser download.
 */
export function exportBundle(selection: Record<string, EntitySelector>) {
  return post('/bundle/export', selection, { responseType: 'blob' })
}

/**
 * POST /bundle/import/preview
 * Upload a bundle file and preview what the import would do.
 */
export function previewBundleImport(file: File) {
  const fd = new FormData()
  fd.append('file', file)
  return post<PreviewResponse>('/bundle/import/preview', fd)
}

/**
 * POST /bundle/import
 * Execute a selective import from a bundle.
 */
export function importBundle(
  bundle: Record<string, unknown>,
  selection: Record<string, EntitySelector>,
) {
  return post<ImportResponse>('/bundle/import', { bundle, ...selection })
}

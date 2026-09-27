interface Props {
  layout: 'grid' | 'list'
  search: string
  sort: 'name' | 'recent' | 'number'
  projects?: boolean
  onLayout: (value: 'grid' | 'list') => void
  onSearch: (value: string) => void
  onSort: (value: 'name' | 'recent' | 'number') => void
}
export default function BrowseControls(p: Props) {
  return <div className="browse-controls">
    <input type="search" className="field" aria-label="Search archive" placeholder={p.projects ? 'Search projects' : 'Search designs'} value={p.search} onChange={e => p.onSearch(e.target.value)} />
    <div className="browse-options">
      <div className="seg" aria-label="Layout">
        {(['grid', 'list'] as const).map(mode => <button key={mode} className={p.layout === mode ? 'on' : ''} aria-pressed={p.layout === mode} onClick={() => p.onLayout(mode)}>
          <svg aria-hidden="true" width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d={mode === 'grid' ? 'M3 3h5v5H3zM12 3h5v5h-5zM3 12h5v5H3zM12 12h5v5h-5z' : 'M3 5h2m3 0h9M3 10h2m3 0h9M3 15h2m3 0h9'} /></svg>
          {mode === 'grid' ? 'Grid' : 'List'}
        </button>)}
      </div>
      <select aria-label="Sort archive" value={p.sort} onChange={e => p.onSort(e.target.value as Props['sort'])}>
        <option value="number">{p.projects ? 'Order' : 'Number'}</option>
        <option value="name">Name A–Z</option>
        {!p.projects && <option value="recent">Recent</option>}
      </select>
      <button className="chip" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>Top</button>
    </div>
  </div>
}

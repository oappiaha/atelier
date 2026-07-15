import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, setToken } from '../lib/api'

export default function Login() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get('token')

  useEffect(() => {
    if (!token) return
    api<{ jwt: string }>('/auth/verify', { method: 'POST', body: JSON.stringify({ token }) })
      .then(({ jwt }) => {
        setToken(jwt)
        navigate('/', { replace: true })
      })
      .catch(() => setError('That link has expired. Request a new one.'))
  }, [token, navigate])

  const send = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      await api('/auth/link', { method: 'POST', body: JSON.stringify({ email }) })
      setSent(true)
    } catch {
      setError('Could not send the link. Is the API running?')
    }
  }

  return (
    <div className="view" style={{ display: 'grid', placeItems: 'center', paddingBottom: 0 }}>
      <div className="panel rise" style={{ width: 'min(380px, calc(100vw - 32px))', padding: 28 }}>
        <div className="syne" style={{ fontSize: 24, fontWeight: 800 }}>Atelier</div>
        <div className="mono" style={{ fontSize: 8.5, letterSpacing: '.2em', color: 'var(--faint)', marginTop: 3 }}>
          DESIGN ARCHIVE
        </div>
        {token && !error ? (
          <p style={{ marginTop: 18, fontSize: 13, color: 'var(--fog)' }}>Signing you in…</p>
        ) : sent ? (
          <p style={{ marginTop: 18, fontSize: 13, color: 'var(--fog)', lineHeight: 1.6 }}>
            Check your email for a sign-in link. It's valid for 15 minutes.
            <br />
            <span className="mono" style={{ fontSize: 10, color: 'var(--faint)' }}>
              (dev: the link is printed in the API log)
            </span>
          </p>
        ) : (
          <form onSubmit={send}>
            <input
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com"
              style={{
                width: '100%', borderRadius: 14, border: '1px solid var(--stroke-strong)',
                background: 'var(--bg)', padding: '12px 14px', fontSize: 13, marginTop: 18, outline: 'none',
              }}
            />
            <button className="primary-btn press" type="submit">Send magic link</button>
          </form>
        )}
        {error && <p style={{ marginTop: 12, fontSize: 12, color: 'var(--warn)' }}>{error}</p>}
      </div>
    </div>
  )
}

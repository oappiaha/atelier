import { useToast } from '../lib/store'

export default function Toast() {
  const { msg, visible } = useToast()
  return (
    <div className={`toast${visible ? ' show' : ''}`} id="toast">
      {msg}
    </div>
  )
}

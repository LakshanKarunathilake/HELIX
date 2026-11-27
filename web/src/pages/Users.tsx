import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, Loader2, MailPlus, Pencil, Users as UsersIcon } from 'lucide-react'
import * as api from '@/api/client'
import type { AdminUser, Invite, InviteCreated, InviteStatus, Role } from '@/types'

const ROLES: Role[] = ['admin', 'reviewer', 'annotator']

const STATUS_STYLE: Record<InviteStatus, string> = {
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  accepted: 'bg-green-50 text-green-700 border-green-200',
  revoked: 'bg-gray-100 text-gray-500 border-gray-200',
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

const Loading = () => (
  <div className="flex items-center justify-center py-10 text-gray-400 gap-2 text-sm">
    <Loader2 className="w-4 h-4 animate-spin" /> Loading…
  </div>
)

const LoadError = ({ what, error }: { what: string; error: unknown }) => (
  <div className="px-6 pb-6 text-sm text-red-600" role="alert">
    Could not load {what}: {errorText(error)}
  </div>
)

/** Checkbox list over the existing sessions; `disabled` while a save is in flight. */
function SessionPicker({ options, selected, onChange, disabled }: {
  options: string[]
  selected: string[]
  onChange: (next: string[]) => void
  disabled?: boolean
}) {
  if (options.length === 0) return <p className="text-xs text-gray-400">No sessions exist yet.</p>
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {options.map(name => (
        <label key={name} className="flex items-center gap-1.5 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={selected.includes(name)}
            disabled={disabled}
            onChange={e => onChange(
              e.target.checked ? [...selected, name] : selected.filter(n => n !== name),
            )}
          />
          {name}
        </label>
      ))}
    </div>
  )
}

/** `revocations` counts successful revokes: each one clears a stale error here (for example a
 * "pending invite" 409 that the revoke just resolved). */
function InviteForm({ sessions, revocations }: { sessions: string[]; revocations: number }) {
  const qc = useQueryClient()
  const [error, setError] = useState('')
  useEffect(() => { setError('') }, [revocations])
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('annotator')
  const [chosen, setChosen] = useState<string[]>([])
  const [copied, setCopied] = useState<'yes' | 'failed' | null>(null)
  const urlRef = useRef<HTMLInputElement>(null)

  const create = useMutation({
    mutationFn: api.createInvite,
    onMutate: () => setError(''),
    onError: e => setError(errorText(e)),
    onSuccess: () => {
      setEmail('')
      setCopied(null)
      qc.invalidateQueries({ queryKey: ['admin', 'invites'] })
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    },
  })
  const created: InviteCreated | undefined = create.data

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setError('')
    create.mutate({ email: email.trim(), role, session_names: chosen })
  }

  const copy = async () => {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.sign_in_url)
      setCopied('yes')
    } catch {
      // Clipboard API unavailable (insecure origin or permission denied): select the text instead.
      urlRef.current?.select()
      setCopied('failed')
    }
  }

  return (
    <section className="card p-6 mb-8">
      <h2 className="text-lg font-semibold text-gray-800 mb-4 flex items-center gap-2">
        <MailPlus className="w-5 h-5 text-gray-400" /> Invite
      </h2>
      <form onSubmit={submit} className="grid gap-4">
        <div className="grid sm:grid-cols-[1fr_12rem] gap-4">
          <div>
            <label className="label" htmlFor="invite-email">Email</label>
            <input
              id="invite-email"
              type="email"
              required
              className="input"
              placeholder="person@example.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="invite-role">Role</label>
            <select
              id="invite-role"
              className="select"
              value={role}
              onChange={e => setRole(e.target.value as Role)}
            >
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        </div>
        <div>
          <span className="label">Sessions</span>
          <SessionPicker options={sessions} selected={chosen} onChange={setChosen} />
        </div>
        <div className="flex items-center gap-3">
          <button className="btn-primary" type="submit" disabled={create.isPending || !email.trim()}>
            {create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <MailPlus className="w-4 h-4" />}
            Create invite
          </button>
          {error && (
            <span className="text-sm text-red-600" role="alert">{error}</span>
          )}
        </div>
      </form>

      {created && (
        <div className="mt-5 rounded-lg border border-green-200 bg-green-50 p-4 text-sm">
          <p className="font-medium text-green-800 mb-2">
            {created.already_registered
              ? `${created.invite.email} is already registered: sessions added.`
              : `Invite created for ${created.invite.email}. Send them this link:`}
          </p>
          <div className="flex items-center gap-2">
            <input
              ref={urlRef}
              readOnly
              aria-label="Sign-in URL"
              className="input font-mono text-xs"
              value={created.sign_in_url}
              onFocus={e => e.currentTarget.select()}
            />
            <button type="button" className="btn-secondary shrink-0" onClick={copy}>
              {copied === 'yes' ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              {copied === 'yes' ? 'Copied' : 'Copy'}
            </button>
          </div>
          {copied === 'failed' && (
            <p className="mt-2 text-xs text-gray-600">Could not copy automatically. The link is selected: press Ctrl/Cmd+C.</p>
          )}
        </div>
      )}
    </section>
  )
}

function InvitesTable({ onRevoked }: { onRevoked: () => void }) {
  const qc = useQueryClient()
  const invites = useQuery({ queryKey: ['admin', 'invites'], queryFn: api.listInvites })
  const [errors, setErrors] = useState<Record<number, string>>({})
  const setError = (id: number, msg: string) => setErrors(prev => ({ ...prev, [id]: msg }))

  const revoke = useMutation({
    mutationFn: (id: number) => api.revokeInvite(id),
    onSuccess: (_r, id) => { setError(id, ''); onRevoked() },
    onError: (e, id) => setError(id, errorText(e)),
    onSettled: () => qc.invalidateQueries({ queryKey: ['admin', 'invites'] }),
  })

  return (
    <section className="card mb-8 overflow-hidden">
      <h2 className="text-lg font-semibold text-gray-800 px-6 pt-6 pb-3">Invites</h2>
      {invites.isLoading && <Loading />}
      {invites.isError && <LoadError what="invites" error={invites.error} />}
      {invites.data && invites.data.length === 0 && (
        <p className="px-6 pb-6 text-sm text-gray-500">No invites yet.</p>
      )}
      {invites.data && invites.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-gray-500 bg-gray-50">
              <tr>
                <th className="px-6 py-2 font-medium">Email</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Sessions</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-6 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {invites.data.map((inv: Invite) => (
                <tr key={inv.id}>
                  <td className="px-6 py-2 text-gray-800">{inv.email}</td>
                  <td className="px-3 py-2 text-gray-600">{inv.role}</td>
                  <td className="px-3 py-2 text-gray-600">{inv.session_names.join(', ') || '-'}</td>
                  <td className="px-3 py-2">
                    <span className={`text-[10px] font-semibold uppercase tracking-wide border rounded-full px-2 py-0.5 ${STATUS_STYLE[inv.status]}`}>
                      {inv.status}
                    </span>
                  </td>
                  <td className="px-6 py-2 text-right">
                    {inv.status === 'pending' && (
                      <button
                        className="btn-danger !py-1"
                        disabled={revoke.isPending && revoke.variables === inv.id}
                        onClick={() => revoke.mutate(inv.id)}
                      >
                        Revoke
                      </button>
                    )}
                    {errors[inv.id] && (
                      <p className="text-xs text-red-600 mt-1" role="alert">{errors[inv.id]}</p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

/** Shows a user's sessions; "Edit" opens a checklist that is saved or discarded as a whole. */
function SessionEditor({ user, sessions, saving, onSave }: {
  user: AdminUser
  sessions: string[]
  saving: boolean
  onSave: (names: string[]) => Promise<unknown>
}) {
  const [draft, setDraft] = useState<string[] | null>(null)

  if (draft === null) {
    return (
      <div className="flex items-start gap-2">
        <span className="text-gray-600">{user.session_names.join(', ') || '-'}</span>
        <button
          className="text-gray-400 hover:text-brand-600 shrink-0"
          aria-label={`Edit sessions for ${user.name}`}
          onClick={() => setDraft(user.session_names)}
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
      </div>
    )
  }
  return (
    <div className="grid gap-2">
      {/* A membership can outlive its session; keep it listed so the admin can drop it. */}
      <SessionPicker
        options={[...new Set([...sessions, ...user.session_names])]}
        selected={draft}
        onChange={setDraft}
        disabled={saving}
      />
      <div className="flex gap-2">
        <button
          className="btn-primary !py-1"
          disabled={saving}
          // On failure the row shows the error and the editor stays open.
          onClick={() => onSave(draft).then(() => setDraft(null), () => {})}
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />} Save
        </button>
        <button className="btn-secondary !py-1" disabled={saving} onClick={() => setDraft(null)}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function UsersTable({ sessions }: { sessions: string[] }) {
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['admin', 'users'], queryFn: api.listUsers })
  const [errors, setErrors] = useState<Record<number, string>>({})
  const setError = (id: number, msg: string) => setErrors(prev => ({ ...prev, [id]: msg }))

  const patch = useMutation({
    mutationFn: (v: { id: number; changes: { role?: Role; active?: boolean } }) =>
      api.patchUser(v.id, v.changes),
    onSuccess: (_r, v) => setError(v.id, ''),
    onError: (e, v) => setError(v.id, errorText(e)),
    onSettled: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })
  const memberships = useMutation({
    mutationFn: (v: { id: number; names: string[] }) => api.setUserSessions(v.id, v.names),
    onSuccess: (_r, v) => setError(v.id, ''),
    onError: (e, v) => setError(v.id, errorText(e)),
    onSettled: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  return (
    <section className="card overflow-hidden">
      <h2 className="text-lg font-semibold text-gray-800 px-6 pt-6 pb-3 flex items-center gap-2">
        <UsersIcon className="w-5 h-5 text-gray-400" /> Users
      </h2>
      {users.isLoading && <Loading />}
      {users.isError && <LoadError what="users" error={users.error} />}
      {users.data && users.data.length === 0 && (
        <p className="px-6 pb-6 text-sm text-gray-500">No users yet.</p>
      )}
      {users.data && users.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-gray-500 bg-gray-50">
              <tr>
                <th className="px-6 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Email</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Active</th>
                <th className="px-3 py-2 font-medium">Sessions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {users.data.map(u => {
                const busy = patch.isPending && patch.variables?.id === u.id
                return (
                  <tr key={u.id} className="align-top">
                    <td className="px-6 py-2 text-gray-800">{u.name}</td>
                    <td className="px-3 py-2 text-gray-600">
                      {u.email}
                      {errors[u.id] && (
                        <p className="text-xs text-red-600 mt-1" role="alert">{errors[u.id]}</p>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <select
                        className="select !py-1 !w-auto"
                        aria-label={`Role for ${u.name}`}
                        value={u.role}
                        disabled={busy}
                        onChange={e => patch.mutate({ id: u.id, changes: { role: e.target.value as Role } })}
                      >
                        {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </td>
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        aria-label={`Active: ${u.name}`}
                        checked={u.active}
                        disabled={busy}
                        onChange={e => patch.mutate({ id: u.id, changes: { active: e.target.checked } })}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <SessionEditor
                        user={u}
                        sessions={sessions}
                        saving={memberships.isPending && memberships.variables?.id === u.id}
                        onSave={names => memberships.mutateAsync({ id: u.id, names })}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function Users() {
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: api.getSessions })
  const [revocations, setRevocations] = useState(0)

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Users and invitations</h1>
      {sessions.isError && (
        <div className="card p-4 mb-6 text-sm text-red-600" role="alert">
          Could not load sessions for the pickers: {errorText(sessions.error)}
        </div>
      )}
      <InviteForm sessions={sessions.data ?? []} revocations={revocations} />
      <InvitesTable onRevoked={() => setRevocations(n => n + 1)} />
      <UsersTable sessions={sessions.data ?? []} />
    </div>
  )
}

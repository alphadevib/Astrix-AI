// Mission Memory page — the spacecraft knowledge profile, lessons, anomaly
// history, retrieval search and the verification audit trail.
//
// This page is where the project's central claim is checkable: that Astrix
// accumulates knowledge rather than just reacting. Everything here is read back
// from persistent storage, so it survives a restart and grows across runs.

import { useCallback, useEffect, useState } from 'react'
import api from '../services/api'
import { INK, SERIES, STATUS } from '../theme'
import { Empty, Panel, Pill, Tile, fmt } from '../components/primitives'

// The last data shown, kept across visits so the page paints at once and the
// fetch refreshes it in place instead of starting from an empty screen.
const last = { profile: null, lessons: [], anomalies: [], audit: [] }

export default function MissionMemory() {
  const [profile, setProfile] = useState(() => last.profile)
  const [lessons, setLessons] = useState(() => last.lessons)
  const [anomalies, setAnomalies] = useState(() => last.anomalies)
  const [audit, setAudit] = useState(() => last.audit)
  const [query, setQuery] = useState('reaction wheel vibration motor current degradation')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    Promise.all([api.profile(), api.lessons(), api.anomalies(40), api.audit(80)])
      .then(([profileData, lessonData, anomalyData, auditData]) => {
        last.profile = profileData
        last.lessons = lessonData.lessons ?? []
        last.anomalies = anomalyData.anomalies ?? []
        last.audit = auditData.audit ?? []
        setProfile(last.profile)
        setLessons(last.lessons)
        setAnomalies(last.anomalies)
        setAudit(last.audit)
      })
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    load()
    // Memory grows while a mission runs; refresh so the page is never stale
    // during a demo.
    const id = window.setInterval(load, 8000)
    return () => window.clearInterval(id)
  }, [load])

  const search = async (event) => {
    event.preventDefault()
    setSearching(true)
    setError(null)
    try {
      setResults(await api.searchMemory(query, 6))
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="grid" style={{ gap: 14 }}>
      {error && (
        <div className="banner" style={{ borderColor: STATUS.critical }}>
          <div className="banner-text">{error}</div>
        </div>
      )}

      {profile && (
        <div className="tile-grid">
          <Tile label="Spacecraft" value={<span style={{ fontSize: 16 }}>{profile.spacecraft_id}</span>} sub={`status ${profile.status}`} />
          <Tile label="Missions flown" value={profile.missions_flown} />
          <Tile
            label="Anomalies on record"
            value={Object.values(profile.anomaly_counts ?? {}).reduce((a, b) => a + b, 0)}
            sub={Object.entries(profile.anomaly_counts ?? {})
              .map(([key, value]) => `${key} ${value}`)
              .join(' · ')}
          />
          <Tile label="Lessons learned" value={lessons.length} />
        </div>
      )}

      <div className="grid cols-2">
        <Panel title="Spacecraft knowledge profile" note="assembled, not hand-maintained">
          {!profile ? (
            <Empty>Loading…</Empty>
          ) : (
            <>
              <h3 className="panel-title" style={{ marginBottom: 6 }}>
                Capabilities
              </h3>
              <table className="data" style={{ marginBottom: 12 }}>
                <tbody>
                  {Object.entries(profile.capabilities ?? {}).map(([name, rating]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td style={{ textAlign: 'right', color: INK.primary }}>{rating}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h3 className="panel-title" style={{ marginBottom: 5 }}>
                Known limitations
              </h3>
              <ul className="clean">
                {profile.known_limitations?.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>

              <h3 className="panel-title" style={{ margin: '12px 0 5px' }}>
                Latent & recurring risks
              </h3>
              {profile.latent_risks?.length ? (
                <ul className="clean">
                  {profile.latent_risks.map((item) => (
                    <li key={item} style={{ color: STATUS.warning }}>
                      {item}
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>None identified across missions yet.</Empty>
              )}

              <h3 className="panel-title" style={{ margin: '12px 0 5px' }}>
                Recoveries that worked
              </h3>
              <ul className="clean">
                {profile.successful_recoveries?.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
        </Panel>

        <div className="grid" style={{ gap: 14, alignContent: 'start' }}>
          <Panel title="Retrieval" note="what the agents query at diagnosis time">
            <form onSubmit={search} className="control-row">
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                style={{ flex: 1, minWidth: 220 }}
                aria-label="Mission memory search"
              />
              <button className="btn primary" type="submit" disabled={searching}>
                {searching ? 'Searching…' : 'Search'}
              </button>
            </form>

            {results && (
              <div style={{ marginTop: 12 }}>
                {results.hits?.length === 0 && <Empty>No document cleared the similarity floor.</Empty>}
                {results.hits?.map((hit) => (
                  <div key={hit.title + hit.similarity} style={{ marginBottom: 10 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span className="mono small" style={{ color: SERIES[0] }}>
                        {fmt.num(hit.similarity, 2)}
                      </span>
                      <strong style={{ fontSize: 13 }}>{hit.title}</strong>
                      <Pill>{hit.source}</Pill>
                    </div>
                    <div className="evidence" style={{ marginTop: 4 }}>
                      {hit.excerpt}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Lessons learned" note={`${lessons.length} on record`}>
            {lessons.length === 0 ? (
              <Empty>No lessons yet.</Empty>
            ) : (
              <ul className="clean">
                {lessons.map((lesson) => (
                  <li key={lesson}>{lesson}</li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>

      <Panel title="Anomaly history" note="suppressed detections included, and labelled">
        {anomalies.length === 0 ? (
          <Empty>No anomalies recorded.</Empty>
        ) : (
          <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>ID</th>
                <th>Time</th>
                <th>Mission</th>
                <th>Subsystem</th>
                <th>Type</th>
                <th>Severity</th>
                <th style={{ textAlign: 'right' }}>Score</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {anomalies.map((anomaly) => (
                <tr key={anomaly.anomaly_id}>
                  <td className="mono">{anomaly.anomaly_id}</td>
                  <td className="mono small">{fmt.time(anomaly.timestamp)}</td>
                  <td>{anomaly.mission_id}</td>
                  <td>{anomaly.subsystem}</td>
                  <td>{fmt.title(anomaly.anomaly_type)}</td>
                  <td style={{ color: anomaly.suppressed ? INK.muted : INK.primary }}>
                    {anomaly.severity}
                    {anomaly.suppressed ? ' (suppressed)' : ''}
                  </td>
                  <td className="num">{fmt.num(anomaly.final_score, 3)}</td>
                  <td>{anomaly.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Panel>

      <Panel title="Verification audit trail" note="append-only">
        {audit.length === 0 ? (
          <Empty>No verification decisions recorded yet.</Empty>
        ) : (
          <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Time</th>
                <th>Stage</th>
                <th>Actor</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((entry, index) => (
                <tr key={`${entry.audit_id}-${index}`}>
                  <td className="mono small">{fmt.time(entry.created_at)}</td>
                  <td>{fmt.title(entry.stage)}</td>
                  <td className="mono small">{entry.actor}</td>
                  <td className="small mono" style={{ wordBreak: 'break-word' }}>
                    {JSON.stringify(entry.detail)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Panel>
    </div>
  )
}

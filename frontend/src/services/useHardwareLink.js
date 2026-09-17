// Console-wide hardware link. Lives above the pages so the board stays
// connected while the operator moves between labs.
//
// Readings are batched to the backend every 250 ms; commands the backend
// queues (operator, assistant or ASTRIX recovery) come back in the response and
// are written to the board.

import { useCallback, useEffect, useRef, useState } from 'react'
import api from './api'
import { EmulatedBoard, SerialBoard, webSerialSupported } from './serial'

const MAX_READINGS = 240
const MAX_LOG = 80

export default function useHardwareLink() {
  const [connected, setConnected] = useState(false)
  const [kind, setKind] = useState(null)
  const [readings, setReadings] = useState([])
  const [log, setLog] = useState([])
  const [server, setServer] = useState(null)
  const [error, setError] = useState(null)
  const boardRef = useRef(null)
  const pendingRef = useRef({ readings: [], acks: [] })
  const bufferRef = useRef([])

  const addLog = useCallback((direction, text, source) => {
    setLog((entries) => [{ direction, text, source, at: Date.now() }, ...entries].slice(0, MAX_LOG))
  }, [])

  const handleClose = useCallback(() => {
    boardRef.current = null
    setConnected(false)
    setKind(null)
  }, [])

  const connect = useCallback(
    async (mode) => {
      setError(null)
      const handlers = {
        onReading: (reading) => {
          pendingRef.current.readings.push(reading)
          bufferRef.current.push({ ...reading, t: Date.now() })
        },
        onAck: (line) => {
          pendingRef.current.acks.push(line)
          addLog('rx', line)
        },
        onClose: handleClose,
      }
      try {
        if (boardRef.current) await boardRef.current.close()
        const board = mode === 'emulator' ? new EmulatedBoard(handlers) : new SerialBoard(handlers)
        await board.open()
        boardRef.current = board
        setConnected(true)
        setKind(board.kind)
        api.hardwareCalibrate().catch(() => {})
      } catch (err) {
        if (err?.name !== 'NotFoundError') setError(err.message ?? String(err))
      }
    },
    [addLog, handleClose],
  )

  const disconnect = useCallback(async () => {
    await boardRef.current?.close()
    handleClose()
    api.hardwareDisconnect().catch(() => {})
  }, [handleClose])

  const send = useCallback(
    async (command) => {
      const board = boardRef.current
      if (board) {
        await board.write(command)
        addLog('tx', command.toUpperCase(), 'operator')
        api.hardwareCommand(command, 'local').catch(() => {})
      } else {
        // No local board: queue it on the backend for a serial bridge to deliver.
        await api.hardwareCommand(command, 'queue')
        addLog('tx', `${command.toUpperCase()} (queued)`, 'operator')
      }
    },
    [addLog],
  )

  // Batch readings to the API and deliver any queued commands.
  useEffect(() => {
    if (!connected) return undefined
    let stopped = false
    const flush = async () => {
      const { readings: batch, acks } = pendingRef.current
      pendingRef.current = { readings: [], acks: [] }
      const incoming = bufferRef.current
      bufferRef.current = []
      if (incoming.length) {
        setReadings((prev) => {
          const next = prev.concat(incoming)
          return next.length > MAX_READINGS ? next.slice(next.length - MAX_READINGS) : next
        })
      }
      if (!batch.length && !acks.length) return
      try {
        const result = await api.hardwareTelemetry(batch.slice(-100), acks.slice(-50), `web-serial:${boardRef.current?.kind ?? 'usb'}`)
        setServer({ calibrated: result.calibrated, at: Date.now() })
        for (const command of result.commands ?? []) {
          if (stopped || !boardRef.current) break
          await boardRef.current.write(command)
          addLog('tx', command, 'astrix')
        }
      } catch (err) {
        setServer({ error: err.message, at: Date.now() })
      }
    }
    const id = window.setInterval(flush, 250)
    return () => {
      stopped = true
      window.clearInterval(id)
    }
  }, [connected, addLog])

  useEffect(() => () => boardRef.current?.close(), [])

  return {
    connected,
    kind,
    readings,
    latest: readings.length ? readings[readings.length - 1] : null,
    log,
    server,
    error,
    supported: webSerialSupported(),
    connect,
    disconnect,
    send,
  }
}

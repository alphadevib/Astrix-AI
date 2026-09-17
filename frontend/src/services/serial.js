// Web Serial link to an Astrix HIL board, plus a software board for demos.
//
// The browser owns the USB port, so this works when the console is served from
// Vercel: readings flow board → browser → API, and commands flow back the same
// way. Web Serial is available in Chromium browsers (Chrome, Edge, Opera) over
// HTTPS or localhost.

export const webSerialSupported = () => typeof navigator !== 'undefined' && 'serial' in navigator

export class SerialBoard {
  constructor({ onReading, onAck, onClose }) {
    this.onReading = onReading
    this.onAck = onAck
    this.onClose = onClose
    this.port = null
    this.reader = null
    this.writer = null
    this.closed = false
    this.kind = 'usb'
  }

  async open(baudRate = 115200) {
    this.port = await navigator.serial.requestPort()
    await this.port.open({ baudRate })
    this.writer = this.port.writable.getWriter()
    this.#readLoop()
  }

  async #readLoop() {
    const decoder = new TextDecoderStream()
    const done = this.port.readable.pipeTo(decoder.writable).catch(() => {})
    this.reader = decoder.readable.getReader()
    let buffer = ''
    try {
      while (!this.closed) {
        const { value, done: finished } = await this.reader.read()
        if (finished) break
        buffer += value
        let index
        while ((index = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, index).trim()
          buffer = buffer.slice(index + 1)
          this.#dispatch(line)
        }
        if (buffer.length > 4096) buffer = ''
      }
    } catch {
      /* device unplugged */
    } finally {
      this.reader?.releaseLock()
      await done
      if (!this.closed) this.onClose?.()
    }
  }

  #dispatch(line) {
    if (!line.startsWith('{')) return
    let data
    try {
      data = JSON.parse(line)
    } catch {
      return
    }
    if ('ack' in data || 'err' in data) this.onAck?.(line, data)
    else this.onReading?.(data)
  }

  async write(command) {
    if (!this.writer) throw new Error('board is not connected')
    await this.writer.write(new TextEncoder().encode(`${command}\n`))
  }

  async close() {
    this.closed = true
    try {
      await this.reader?.cancel()
      this.writer?.releaseLock()
      await this.port?.close()
    } catch {
      /* already closed */
    }
    this.onClose?.()
  }
}

// Mirrors backend/app/hardware/bridge.py EmulatedBoard and the firmware.
export class EmulatedBoard {
  constructor({ onReading, onAck, onClose }) {
    this.onReading = onReading
    this.onAck = onAck
    this.onClose = onClose
    this.kind = 'emulator'
    this.seq = 0
    this.fault = 'NONE'
    this.sev = 0
    this.faultAt = 0
    this.mode = 'NOMINAL'
    this.motor = 180
    this.stuck = null
    this.rateMs = 200
    this.timer = null
    this.start = performance.now()
  }

  async open() {
    this.onAck?.('{"ack":"BOOT","ok":true,"dev":"astrix-hil-emulator","fw":"1.0.0"}')
    this.timer = window.setInterval(() => this.#tick(), this.rateMs)
  }

  #noise(scale) {
    return (Math.random() - 0.5) * 2 * scale
  }

  #tick() {
    this.seq += 1
    const t = (performance.now() - this.start) / 1000
    const ramp = this.fault === 'NONE' ? 0 : Math.min(1, (performance.now() - this.faultAt) / 20000) * this.sev
    let load = this.mode === 'SAFE' ? 0.35 : this.mode === 'POWER_SAVE' ? 0.42 : 0.55
    let temp = 24 + 0.6 * Math.sin(t / 30) + this.#noise(0.05) + (3 * this.motor) / 255
    if (this.fault === 'TEMP_BIAS') temp += 15 * ramp
    if (this.fault === 'SENSOR_STUCK') {
      this.stuck ??= temp
      temp = this.stuck
    }
    let vib = 0.08 + (0.12 * this.motor) / 255 + Math.abs(this.#noise(0.01))
    if (this.fault === 'VIB_SPIKE' && this.motor > 0) vib += 1.6 * ramp
    let rpm = (4200 * this.motor) / 255 + this.#noise(15)
    if (this.fault === 'MOTOR_STALL') {
      rpm *= 1 - 0.8 * ramp
      load += 0.3 * ramp
    }
    const gyroBias = this.fault === 'GYRO_DRIFT' ? [4 * ramp, -3 * ramp, 2 * ramp] : [0, 0, 0]
    let current = load + (0.25 * this.motor) / 255 + this.#noise(0.005)
    if (this.fault === 'OVERCURRENT') current += 0.8 * ramp
    let busV = 5.02 - 0.08 * current + this.#noise(0.003)
    if (this.fault === 'BROWNOUT') busV -= 1.2 * ramp
    if (this.fault === 'DROPOUT' && Math.random() < this.sev * 0.8) return
    this.onReading?.({
      dev: 'astrix-hil-emulator',
      fw: '1.0.0',
      seq: this.seq,
      ms: Math.round(t * 1000),
      temp_c: +temp.toFixed(2),
      bus_v: +Math.max(0, busV).toFixed(3),
      current_a: +Math.max(0, current).toFixed(3),
      light: +(0.72 + 0.02 * Math.sin(t / 50)).toFixed(3),
      vib_g: +vib.toFixed(3),
      gyro: gyroBias.map((b) => +(b + this.#noise(0.05)).toFixed(3)),
      rpm: +Math.max(0, rpm).toFixed(1),
      fault: this.fault,
      sev: this.sev,
      mode: this.mode,
    })
  }

  async write(command) {
    const [cmd, a1, a2] = command.trim().toUpperCase().split(/\s+/)
    if (cmd === 'INJECT') {
      this.fault = a1
      this.sev = Number(a2)
      this.faultAt = performance.now()
      this.stuck = null
    } else if (cmd === 'CLEAR') {
      this.fault = 'NONE'
      this.sev = 0
      this.stuck = null
    } else if (cmd === 'MOTOR') {
      this.motor = Math.max(0, Math.min(255, Number(a1)))
    } else if (cmd === 'RATE') {
      this.rateMs = 1000 / Math.max(1, Math.min(20, Number(a1)))
      window.clearInterval(this.timer)
      this.timer = window.setInterval(() => this.#tick(), this.rateMs)
    } else if (cmd === 'ACT') {
      if (a1 === 'ISOLATE_WHEEL') {
        this.motor = 0
        if (['VIB_SPIKE', 'MOTOR_STALL'].includes(this.fault)) this.fault = 'NONE'
      } else if (a1 === 'RESTART_WHEEL') this.motor = 180
      else if (a1 === 'WHEEL_SPEED') this.motor = Math.round((255 * Number(a2)) / 100)
      else if (a1 === 'SAFE_MODE') this.mode = 'SAFE'
      else if (a1 === 'POWER_SAVE') this.mode = 'POWER_SAVE'
      else if (a1 === 'NOMINAL_MODE') this.mode = 'NOMINAL'
      else if (a1 === 'REDUNDANT_SENSOR' && ['TEMP_BIAS', 'SENSOR_STUCK'].includes(this.fault)) this.fault = 'NONE'
      else if (a1 === 'RECAL_GYRO' && this.fault === 'GYRO_DRIFT') this.fault = 'NONE'
    }
    window.setTimeout(() => this.onAck?.(JSON.stringify({ ack: command.toUpperCase(), ok: true })), 20)
  }

  async close() {
    window.clearInterval(this.timer)
    this.onClose?.()
  }
}

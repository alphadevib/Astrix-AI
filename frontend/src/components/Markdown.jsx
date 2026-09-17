// Minimal, safe markdown for assistant replies: paragraphs, bullet lists,
// **bold**, *italic* and `code`. Builds React elements — never innerHTML — so
// model output cannot inject markup.

function inline(text, keyPrefix) {
  const parts = []
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g
  let last = 0
  let match
  let i = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    const token = match[0]
    const key = `${keyPrefix}-${i++}`
    if (token.startsWith('**')) parts.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    else if (token.startsWith('`')) parts.push(<code key={key}>{token.slice(1, -1)}</code>)
    else parts.push(<em key={key}>{token.slice(1, -1)}</em>)
    last = match.index + token.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

export default function Markdown({ text }) {
  const blocks = (text ?? '').split(/\n{2,}/)
  return (
    <div className="md">
      {blocks.map((block, b) => {
        const lines = block.split('\n')
        if (lines.every((line) => /^\s*[-*]\s+/.test(line))) {
          return (
            <ul key={b}>
              {lines.map((line, l) => (
                <li key={l}>{inline(line.replace(/^\s*[-*]\s+/, ''), `${b}-${l}`)}</li>
              ))}
            </ul>
          )
        }
        return (
          <p key={b}>
            {lines.map((line, l) => (
              <span key={l}>
                {l > 0 && <br />}
                {inline(line, `${b}-${l}`)}
              </span>
            ))}
          </p>
        )
      })}
    </div>
  )
}

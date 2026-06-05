import { useState, useCallback } from 'react';
import { postAsk } from '../api';
import { Sparkles, Send, Loader } from 'lucide-react';

const EXAMPLE_QUESTIONS = [
  'Which service had the worst staleness last hour?',
  'Show me all keys breaching their SLA',
  'What is the average staleness for the auth service?',
];

export default function AskBar() {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = useCallback(async (q) => {
    const text = q || question;
    if (!text.trim()) return;
    setLoading(true);
    setError('');
    setAnswer('');
    try {
      const { data } = await postAsk(text.trim());
      setAnswer(data.answer);
    } catch (err) {
      setError('Failed to get answer. Is the API running?');
    } finally {
      setLoading(false);
    }
  }, [question]);

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="card card-elevated" style={{ marginBottom: 28 }}>
      <div className="card-header">
        <div className="card-title">
          <div className="card-title-icon" style={{ background: 'rgba(99,102,241,0.15)' }}>
            <Sparkles size={15} color="var(--accent-light)" />
          </div>
          Ask in Natural Language
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {EXAMPLE_QUESTIONS.map((q) => (
            <button
              key={q}
              onClick={() => { setQuestion(q); handleSubmit(q); }}
              style={{
                background: 'var(--bg-input)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                padding: '3px 10px',
                fontSize: '0.7rem',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                transition: 'var(--transition)',
              }}
              onMouseEnter={e => e.target.style.color = 'var(--text-secondary)'}
              onMouseLeave={e => e.target.style.color = 'var(--text-muted)'}
            >
              {q.substring(0, 22)}…
            </button>
          ))}
        </div>
      </div>

      <div className="ask-bar-wrap">
        <div className="ask-input-wrap">
          <Sparkles size={16} className="ask-icon" />
          <input
            id="ask-input"
            type="text"
            className="ask-input"
            placeholder="e.g. Which service had worst staleness last week?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKey}
          />
        </div>
        <button
          id="ask-submit-btn"
          className="ask-submit"
          onClick={() => handleSubmit()}
          disabled={loading || !question.trim()}
        >
          {loading ? <span className="spinner" /> : <Send size={15} />}
          {loading ? 'Thinking…' : 'Ask AI'}
        </button>
      </div>

      {error && (
        <div style={{ color: 'var(--red)', fontSize: '0.82rem', padding: '8px 4px' }}>
          ⚠ {error}
        </div>
      )}

      {answer && (
        <div className="ask-answer" id="ask-answer-result">
          {answer}
        </div>
      )}
    </div>
  );
}

import React, { useState } from 'react';

export function Editor({ save }: { save: (text: string) => Promise<void> }) {
  const [text, setText] = useState('');
  return <textarea value={text} onChange={(e) => setText(e.target.value)} />;
}

import { useState, type ReactNode } from 'react';

type Props = {
  id: string;
  summary: ReactNode;
  detail: ReactNode;
  defaultExpanded?: boolean;
};

export default function ExpandableRow({ id, summary, detail, defaultExpanded }: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded || false);

  return (
    <>
      <tr onClick={() => setExpanded(!expanded)} style={{ cursor:'pointer', transition:'background .15s' }}>
        <td colSpan={99} style={{ padding:0 }}>
          {summary}
        </td>
      </tr>
      {expanded && (
        <tr key={id + '-detail'} style={{ borderTop:'1px solid rgba(30,37,53,0.5)', background:'rgba(16,22,37,0.5)' }}>
          <td colSpan={99} style={{ padding:'6px 14px' }}>
            {detail}
          </td>
        </tr>
      )}
    </>
  );
}

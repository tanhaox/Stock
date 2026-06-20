import { useState, useCallback } from 'react';

export function useDeepAnalysis() {
  const [selectedStocks, setSelectedStocks] = useState<Set<string>>(new Set());
  const [showDeepAnalysis, setShowDeepAnalysis] = useState(false);
  const [deepLoading, setDeepLoading] = useState(false);

  const toggleStock = useCallback((symbol: string) => {
    setSelectedStocks(prev => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else if (next.size < 3) next.add(symbol);
      return next;
    });
  }, []);

  const start = useCallback(() => { setShowDeepAnalysis(true); setDeepLoading(true); }, []);
  const close = useCallback(() => { setShowDeepAnalysis(false); setDeepLoading(false); }, []);
  const complete = useCallback(() => { setDeepLoading(false); }, []);

  return { selected: selectedStocks, toggle: toggleStock, open: showDeepAnalysis, loading: deepLoading, start, close, complete };
}

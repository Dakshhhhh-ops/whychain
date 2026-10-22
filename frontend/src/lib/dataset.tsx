// Which dataset (workspace) the app is looking at. Remembered per browser.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type DatasetInfo = {
  key: string;
  name: string;
  real: boolean;
  description: string;
  available: boolean;
  as_of: string;
  company?: string;
  row_counts: Record<string, number>;
  optimizer: boolean;
  samples: { title: string; tag: string; question: string }[];
  currency: string;
  approval_threshold: number;
  source?: string;
};

type Ctx = { key: string; info?: DatasetInfo; all: DatasetInfo[]; setKey: (k: string) => void };
const DatasetContext = createContext<Ctx>({ key: "", all: [], setKey: () => {} });
const STORE = "whychain.dataset";

export function DatasetProvider({ children }: { children: ReactNode }) {
  const [all, setAll] = useState<DatasetInfo[]>([]);
  const [key, setKeyState] = useState<string>(() => {
    try {
      return localStorage.getItem(STORE) ?? "";
    } catch {
      return "";
    }
  });
  useEffect(() => {
    fetch("/api/datasets")
      .then((r) => r.json())
      .then((d: { default: string; datasets: DatasetInfo[] }) => {
        setAll(d.datasets);
        setKeyState((k) => (k && d.datasets.some((x) => x.key === k && x.available) ? k : d.default));
      })
      .catch(() => {});
  }, []);
  const setKey = (k: string) => {
    setKeyState(k);
    try {
      localStorage.setItem(STORE, k);
    } catch {
      /* private mode */
    }
  };
  return <DatasetContext.Provider value={{ key, info: all.find((d) => d.key === key), all, setKey }}>{children}</DatasetContext.Provider>;
}

export const useDataset = () => useContext(DatasetContext);

export function money(v: number | null | undefined, currency = "INR") {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  const locale = currency === "BRL" ? "pt-BR" : "en-IN";
  return new Intl.NumberFormat(locale, { style: "currency", currency, maximumFractionDigits: 0 }).format(v);
}

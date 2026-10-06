import { demoSetup, demoXml } from "@/lib/demo-match";

export default function ExportPage() {
  const { home, away } = demoXml();
  return (
    <>
      <h1>Export Once Sport XMLs</h1>
      <p className="lead">
        One analysis XML per team. Tag labels are written exactly as configured —
        never renamed. Full match timeline, no video clipping.
      </p>
      <div className="grid">
        <section className="card">
          <h2>{demoSetup.homeTeam} · Home.xml</h2>
          <p className="muted">{home.split("\n").length} lines</p>
          <pre
            style={{
              maxHeight: 320,
              overflow: "auto",
              fontSize: 12,
              background: "#0a1018",
              padding: "0.75rem",
              borderRadius: 10,
            }}
          >
            {home}
          </pre>
        </section>
        <section className="card">
          <h2>{demoSetup.awayTeam} · Away.xml</h2>
          <p className="muted">{away.split("\n").length} lines</p>
          <pre
            style={{
              maxHeight: 320,
              overflow: "auto",
              fontSize: 12,
              background: "#0a1018",
              padding: "0.75rem",
              borderRadius: 10,
            }}
          >
            {away}
          </pre>
        </section>
      </div>
    </>
  );
}

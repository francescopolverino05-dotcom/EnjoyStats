import { BLOCK_MIN_EVENTS } from "@statman/core";
import { demoCoverage } from "@/lib/demo-match";

export default function TaggingPage() {
  const coverage = demoCoverage();
  const needing = coverage.filter((b) => b.needsRepass).length;
  return (
    <>
      <h1>Tagging pipeline</h1>
      <p className="lead">
        Full match processed in 5-minute blocks. Blocks under {BLOCK_MIN_EVENTS} events
        are marked for re-pass. Demo coverage below.
      </p>
      <p>
        <span className="badge">{needing} blocks need re-pass</span>
      </p>
      <section className="card" style={{ marginTop: "1rem", overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Block</th>
              <th>Window</th>
              <th>Home</th>
              <th>Away</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {coverage.slice(0, 18).map((b) => (
              <tr key={b.blockIndex}>
                <td>{b.blockIndex + 1}</td>
                <td>
                  {Math.floor(b.startSeconds / 60)}′–{Math.floor(b.endSeconds / 60)}′
                </td>
                <td>{b.homeEvents}</td>
                <td>{b.awayEvents}</td>
                <td>{b.needsRepass ? "re-pass" : "ok"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">Showing first 18 of {coverage.length} blocks.</p>
      </section>
    </>
  );
}

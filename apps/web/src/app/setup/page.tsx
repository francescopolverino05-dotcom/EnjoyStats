import { demoSetup, demoTags } from "@/lib/demo-match";

export default function SetupPage() {
  const tags = demoTags();
  return (
    <>
      <h1>Match setup</h1>
      <p className="lead">
        Teams, kits, attack direction, line-ups, and video. Demo match is prefilled
        (Pisa vs Perugia) while the DB-backed form ships next.
      </p>
      <div className="grid">
        <section className="card">
          <h2>Fixture</h2>
          <label>Home team</label>
          <input defaultValue={demoSetup.homeTeam} readOnly />
          <label>Away team</label>
          <input defaultValue={demoSetup.awayTeam} readOnly />
          <label>Home kit</label>
          <input defaultValue={demoSetup.homeKitColor} readOnly />
          <label>Away kit</label>
          <input defaultValue={demoSetup.awayKitColor} readOnly />
          <label>Home attacks (1H)</label>
          <input
            defaultValue={
              demoSetup.homeAttacksLeftToRightFirstHalf ? "Left → right" : "Right → left"
            }
            readOnly
          />
        </section>
        <section className="card">
          <h2>Line-ups</h2>
          <table>
            <thead>
              <tr>
                <th>Side</th>
                <th>#</th>
                <th>Player</th>
                <th>Pos</th>
              </tr>
            </thead>
            <tbody>
              {demoSetup.lineups.map((p) => (
                <tr key={p.id}>
                  <td>{p.side}</td>
                  <td>{p.number ?? "—"}</td>
                  <td>{p.name}</td>
                  <td>{p.position ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <h2>Tag panel ({tags.length})</h2>
          <p className="muted">
            Exact Once Sport names — configurable via{" "}
            <code>packages/core/src/tags/default-tags.json</code>.
          </p>
          <ul className="muted">
            {tags.slice(0, 12).map((t) => (
              <li key={t.id}>
                {t.label} → <strong>{t.onceSportName}</strong>
              </li>
            ))}
            <li>…</li>
          </ul>
        </section>
      </div>
    </>
  );
}

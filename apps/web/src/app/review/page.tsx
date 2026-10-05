import { demoEvents } from "@/lib/demo-match";

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}' ${String(s).padStart(2, "0")}`;
}

export default function ReviewPage() {
  return (
    <>
      <h1>Review</h1>
      <p className="lead">
        Timeline of tagged events. Edit / delete / add controls hook up to the DB
        API next — demo list is read-only.
      </p>
      <section className="card" style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Clock</th>
              <th>Tag</th>
              <th>Once Sport</th>
              <th>Team</th>
              <th>Player</th>
              <th>OK?</th>
              <th>x,y</th>
            </tr>
          </thead>
          <tbody>
            {demoEvents.map((e) => (
              <tr key={e.id}>
                <td>{clock(e.clockSeconds)}</td>
                <td>{e.tagId}</td>
                <td>{e.onceSportName}</td>
                <td>{e.side}</td>
                <td>
                  {e.player.number != null ? `#${e.player.number} ` : ""}
                  {e.player.name}
                  {e.player.confidence === "low" ? " · low" : ""}
                </td>
                <td>{e.successful ? "yes" : "no"}</td>
                <td>
                  {e.x.toFixed(0)},{e.y.toFixed(0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

frappe.ui.form.on("Job Applicant", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Re-score ATS"),
			() => {
				frappe.call({
					method: "upande_ats.engine.score_applicant_now",
					args: { applicant: frm.doc.name, opening: frm.doc.job_title },
					freeze: true,
					freeze_message: __("Scoring CV..."),
				}).then((r) => {
					if (r.message) {
						frappe.show_alert({
							message: __("Scored: {0}%", [r.message.score_pct]),
							indicator: "green",
						});
						frm.reload_doc();
					} else {
						frappe.msgprint(__("Could not score: missing Job Opening or no ATS keywords configured."));
					}
				});
			},
			__("ATS")
		);

		frm.add_custom_button(
			__("Re-run Screening"),
			() => {
				frappe.call({
					method: "upande_ats.screening.run_screening",
					args: { applicant: frm.doc.name },
					freeze: true,
					freeze_message: __("Screening applicant..."),
				}).then((r) => {
					if (r.message) {
						frappe.show_alert({
							message: __("Result: {0}", [r.message.result]),
							indicator: r.message.result === "Pass" ? "green" : "orange",
						});
						frm.reload_doc();
					}
				});
			},
			__("ATS")
		);

		if (frm.doc.ats_score_link) {
			frm.add_custom_button(
				__("View Breakdown"),
				() => render_breakdown(frm),
				__("ATS")
			);
		}

		render_score_widget(frm);
	},
});

function render_score_widget(frm) {
	const wrapper = frm.fields_dict.ats_breakdown_html?.$wrapper;
	if (!wrapper) return;
	const score = frm.doc.ats_score;
	const passes = frm.doc.ats_passes_passmark;
	if (score === undefined || score === null) {
		wrapper.html("<div class='text-muted'>Not yet scored.</div>");
		return;
	}
	let color = "red";
	if (score >= 70) color = "green";
	else if (score >= 40) color = "orange";
	const badge = passes
		? "<span class='indicator-pill green'>Passes</span>"
		: "<span class='indicator-pill red'>Below passmark</span>";
	wrapper.html(`
		<div style="display:flex;align-items:center;gap:18px;padding:12px;background:var(--bg-light-gray);border-radius:8px;">
			<div style="font-size:36px;font-weight:600;color:${color};">${Number(score).toFixed(1)}%</div>
			<div>${badge}</div>
		</div>
	`);
}

function render_breakdown(frm) {
	frappe.db.get_doc("ATS Score", frm.doc.ats_score_link).then((doc) => {
		const breakdown = JSON.parse(doc.score_breakdown || "{}");
		const rows = [];
		for (const [engine, data] of Object.entries(breakdown)) {
			rows.push(
				`<tr><td><b>${frappe.utils.escape_html(engine)}</b></td>` +
				`<td>${(data.sub_score ?? 0).toFixed(2)}%</td></tr>`
			);
			if (data.matched && data.matched.length) {
				const kws = data.matched
					.map((m) => `<span class='indicator-pill green' style='margin:2px'>${frappe.utils.escape_html(m.keyword)} (w${m.weight})</span>`)
					.join("");
				rows.push(`<tr><td colspan='2'><div>Matched: ${kws}</div></td></tr>`);
			}
			if (data.missing && data.missing.length) {
				const kws = data.missing
					.map((m) => `<span class='indicator-pill red' style='margin:2px'>${frappe.utils.escape_html(m.keyword)}</span>`)
					.join("");
				rows.push(`<tr><td colspan='2'><div>Missing: ${kws}</div></td></tr>`);
			}
		}
		const html = `
			<div>
				<div style="margin-bottom:8px">
					<b>Final Score:</b> ${doc.score_pct}% &nbsp;
					<b>Passmark:</b> ${doc.passmark_used}% &nbsp;
					<b>Passes:</b> ${doc.passes_passmark ? "Yes" : "No"}
				</div>
				${doc.mandatory_unmatched ? `<div class='text-danger'><b>Mandatory unmatched:</b> ${frappe.utils.escape_html(doc.mandatory_unmatched)}</div>` : ""}
				${doc.note ? `<div class='text-muted'>${frappe.utils.escape_html(doc.note)}</div>` : ""}
				<table class='table table-bordered' style='margin-top:8px'>${rows.join("")}</table>
			</div>`;
		const d = new frappe.ui.Dialog({ title: __("ATS Score Breakdown"), size: "large" });
		d.$body.html(html);
		d.show();
	});
}

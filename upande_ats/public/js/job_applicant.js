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

		frm.add_custom_button(
			__("View Breakdown"),
			() => render_breakdown(frm),
			__("ATS")
		);

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
	// Branch on the LIVE verdict before fetching anything: a Not Scored (or never
	// scored / un-linked) applicant has no current ATS Score to show. Rendering the
	// stale linked record here is how old snapshots leaked back after a Not Scored re-run.
	if (!frm.doc.ats_result || frm.doc.ats_result === "Not Scored" || !frm.doc.ats_score_link) {
		const d = new frappe.ui.Dialog({ title: __("ATS Score Breakdown"), size: "large" });
		d.$body.html(
			`<div class='text-muted'>${__(
				"Not Scored — this applicant has no current ATS result " +
				"(no keywords on the opening, no resume, or no linked opening). " +
				"Re-run screening once configured."
			)}</div>`
		);
		d.show();
		return;
	}
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
					${overall_verdict_html(doc)}
					<b>Keyword gate:</b> ${doc.passes_passmark ? "Pass" : "Fail"}
					(${doc.score_pct}%, passmark ${doc.passmark_used}%)
				</div>
				${doc.mandatory_unmatched ? `<div class='text-danger'><b>Mandatory unmatched:</b> ${frappe.utils.escape_html(doc.mandatory_unmatched)}</div>` : ""}
				${doc.note ? `<div class='text-muted'>${frappe.utils.escape_html(doc.note)}</div>` : ""}
				<table class='table table-bordered' style='margin-top:8px'>${rows.join("")}</table>
				${experience_section_html(doc)}
			</div>`;
		const d = new frappe.ui.Dialog({ title: __("ATS Score Breakdown"), size: "large" });
		d.$body.html(html);
		d.show();
	});
}

// Overall verdict ("Passes") — combines the keyword gate AND the experience gate,
// not the keyword gate alone.
function overall_verdict(doc) {
	const exp = doc.experience_status; // "Pass" | "Fail" | "Needs Review" | "Off"
	if (!doc.passes_passmark) return "No";
	if (exp === "Fail") return "No";
	if (exp === "Needs Review") return "Needs Review";
	return "Yes"; // keyword passed and experience is Pass or Off
}

function overall_verdict_html(doc) {
	const verdict = overall_verdict(doc);
	const color = verdict === "Yes" ? "green" : verdict === "Needs Review" ? "orange" : "red";
	return `<b>Passes:</b> <span class='indicator-pill ${color}'>${verdict}</span> &nbsp;`;
}

function experience_section_html(doc) {
	const required = Number(doc.required_experience || 0);
	if (!(required > 0)) {
		// Gate was off for this opening — say so rather than implying a missed requirement.
		return `<div class='text-muted' style='margin-top:8px'>${__("Experience gate: off for this opening")}</div>`;
	}

	const relevant = Number(doc.relevant_experience || 0);
	const total = Number(doc.total_experience || 0);
	const below = relevant < required;
	const relevant_color = below ? "text-danger" : "";

	let breakdown_rows = "";
	try {
		const roles = JSON.parse(doc.experience_breakdown || "[]");
		breakdown_rows = roles
			.map((r) => {
				const months = r.months != null ? `${r.months} months` : "";
				const tag = r.relevant
					? "<span class='indicator-pill green'>Counted</span>"
					: "<span class='indicator-pill gray'>Not relevant</span>";
				return `<li>${frappe.utils.escape_html(r.role_context || "(role)")} — ${months} — ${tag}</li>`;
			})
			.join("");
	} catch (e) {
		breakdown_rows = "";
	}

	const uncertain_note =
		doc.experience_status === "Needs Review"
			? `<div class='text-warning'>${__("Could not parse work history to confirm relevant experience.")}</div>`
			: "";

	return `
		<div style="margin-top:14px">
			<h6>${__("Experience")}</h6>
			<div class='${relevant_color}'>
				<b>Relevant experience:</b> ${relevant.toFixed(1)} yrs (required ${required.toFixed(1)})
			</div>
			<div><b>Total experience:</b> ${total.toFixed(1)} yrs</div>
			${uncertain_note}
			${breakdown_rows ? `<ul style='margin-top:6px'>${breakdown_rows}</ul>` : ""}
		</div>`;
}

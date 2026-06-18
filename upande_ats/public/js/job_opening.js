frappe.ui.form.on("Job Opening", {
	designation(frm) {
		// On designation set/change, pull that role's standard ATS keywords and
		// append any that aren't already in the table. Additive only: never removes
		// or overwrites rows HR added/edited, and never duplicates an existing keyword.
		if (!frm.doc.designation) return;

		frappe.call({
			method: "upande_ats.api.get_designation_keywords",
			args: { designation: frm.doc.designation },
		}).then((r) => {
			const defaults = (r && r.message) || [];
			if (!defaults.length) return;

			const existing = new Set(
				(frm.doc.ats_keywords || [])
					.map((row) => (row.keyword || "").trim().toLowerCase())
					.filter(Boolean)
			);

			let added = 0;
			defaults.forEach((kw) => {
				const key = (kw.keyword || "").trim().toLowerCase();
				if (!key || existing.has(key)) return;
				const row = frm.add_child("ats_keywords", {
					keyword: kw.keyword,
					weight: kw.weight,
					category: kw.category,
					is_mandatory: kw.is_mandatory,
				});
				existing.add(key);
				added += 1;
			});

			if (added) {
				frm.refresh_field("ats_keywords");
				frappe.show_alert({
					message: __("Added {0} standard ATS keyword(s) for this designation.", [added]),
					indicator: "green",
				});
			}
		});
	},

	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Re-score All Candidates"),
			() => {
				frappe.confirm(
					__("Queue ATS scoring for every applicant of this opening?"),
					() => {
						frappe.call({
							method: "upande_ats.engine.rescore_opening",
							args: { opening: frm.doc.name },
							freeze: true,
						}).then((r) => {
							if (r.message) {
								frappe.show_alert({
									message: __("Queued {0} scoring jobs.", [r.message.queued]),
									indicator: "blue",
								});
							}
						});
					}
				);
			},
			__("ATS")
		);

		frm.add_custom_button(
			__("View Shortlist"),
			() => {
				frappe.route_options = {
					job_title: frm.doc.name,
					ats_passes_passmark: 1,
				};
				frappe.set_route("List", "Job Applicant");
			},
			__("ATS")
		);
	},
});

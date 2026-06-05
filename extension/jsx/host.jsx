/**
 * host.jsx — ExtendScript running inside Premiere Pro's engine.
 * Called from the CEP panel via evalScript().
 */

function getProjectInfo() {
    try {
        if (typeof app === "undefined" || !app.project) {
            return JSON.stringify({ project: null, sequence: null });
        }

        var projectName = null;
        var projectPath = app.project.path;

        if (projectPath && projectPath.length > 0) {
            // Extract filename from full path, remove .prproj extension and unsaved marker
            var parts = projectPath.replace(/\\/g, "/").split("/");
            var filename = parts[parts.length - 1];
            projectName = filename
                .replace(/\.prproj$/i, "")
                .replace(/^\*\s*/, "")
                .replace(/\s*\*$/, "")
                .replace(/\s+/g, " ")
                .trim();
        }

        var sequenceName = null;
        try {
            if (app.project.activeSequence) {
                sequenceName = app.project.activeSequence.name;
            }
        } catch (e) { /* no active sequence */ }

        return JSON.stringify({
            project: projectName || "(no project open)",
            sequence: sequenceName
        });

    } catch (e) {
        return JSON.stringify({ project: null, sequence: null, error: e.toString() });
    }
}

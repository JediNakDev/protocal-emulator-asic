# Release builds

Publishing a GitHub release starts the project's physical CI for the release tag's source commit.
Both stable releases and prereleases use the `published` event, following [GitHub's release-event behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release).
Use prereleases for intermediate area and timing measurements and a stable release for the final reviewed design.

## What runs

| Workflow | Jobs and results |
| --- | --- |
| [gds](../.github/workflows/gds.yaml) | CMOS5L hardening, then precheck, functional gate-level simulation, and GitHub Pages layout viewer deployment |
| [timing](../.github/workflows/timing.yaml) | Global-route setup and hold estimates at slow, typical, and fast corners; timing reports and a job summary |

The two workflows run independently for the same published tag.
The `gds` follow-up jobs consume the GDS build from their own run.
Normal branch and tag pushes, draft releases, and release-note edits do not trigger these workflows.
The existing test, model, and datasheet workflows continue to run on pushes.

## Prepare the source

1. Pull the intended source and run `make check`.
2. Review the RTL, physical configuration, source lists, pin descriptions, and protocol limits together.
3. Commit and push the complete source, then check the push workflows on that commit.
4. Choose a unique release tag that points to this checked commit and includes these release-triggered workflow definitions.

## Publish and review

1. Create a GitHub release for the selected tag and record the source commit in its notes.
2. For an intermediate build, mark it as a prerelease.
3. Publish the release to start `gds` and `timing`.
4. Open both runs in Actions and confirm that their source commit matches the tag.
5. Review hardening, precheck, and gate-level results and download the build artifacts and timing reports.
6. Record the source commit, configuration, run links, area, and final setup/hold results in [area-log.md](area-log.md).
7. Add the run links and measured limitations to the release notes after review.

A published release starts validation; its publication alone does not mean the design passes.
The timing workflow stops before detailed routing, so use the final GDS reports to establish operating limits.
Review every intended corner even if the Tiny Tapeout flow's configured sign-off corner passes.
Gate-level tests are functional and do not include delay annotation.

To retry a transient CI failure, rerun the failed jobs from the existing Actions run.
To build changed source, publish a new tag and release rather than moving a tag whose artifacts have already been recorded.

## Layout viewer

Enable GitHub Pages with **Source: GitHub Actions** in the repository settings.
If the `github-pages` environment restricts deployment refs, permit the tags used for releases.
The viewer job deploys the layout after GDS generation; confirm precheck and gate-level status separately when reviewing a release.

## Local physical builds

Full local hardening needs Docker, LibreLane, and the CMOS5L PDK used by [the Tiny Tapeout CMOS5L action](https://github.com/TinyTapeout/tt-gds-action/tree/ihp-cmos5l).
Use `ihp-sg13cmos5l` and the project's `src/config.json`.
Record the tool and PDK revisions alongside measurements so they can be compared with CI results.

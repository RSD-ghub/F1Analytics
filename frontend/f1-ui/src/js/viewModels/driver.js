define([
    "knockout",
    "ojs/ojarraydataprovider",
    "ojs/ojconverter-datetime",
    "ojs/ojrouter",
    "ojs/ojtable",
    "ojs/ojbutton",
    "ojs/ojchart",
    "ojs/ojselectsingle"
], function (ko, ArrayDataProvider, DateTimeConverter, Router) {

    return function DriverVM(params) {
        const self = this;
        const ALL_SEASONS_VALUE = "ALL";

        self.selectedDriver = ko.observable("");
        self.selectedSeason = ko.observable(ALL_SEASONS_VALUE);
        self.driverOptions = ko.observableArray([]);
        self.seasonOptions = ko.observableArray([]);
        self.analytics = ko.observable(emptyDriverAnalytics(""));
        self.raceResults = ko.observableArray([]);
        self.loading = ko.observable(false);
        self.errorMessage = ko.observable("");

        let suppressAutoRefresh = false;

        self.driverOptionsDp = ko.pureComputed(function () {
            return new ArrayDataProvider(self.driverOptions(), { keyAttributes: "value" });
        });

        self.seasonOptionsDp = ko.pureComputed(function () {
            return new ArrayDataProvider(self.seasonOptions(), { keyAttributes: "value" });
        });

        self.resultsDp = new ArrayDataProvider(self.raceResults, { keyAttributes: "rowKey" });

        self.createdConverter = new DateTimeConverter.IntlDateTimeConverter({
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit"
        });

        self.columns = [
            { headerText: "Season", field: "season" },
            { headerText: "Round", field: "round" },
            { headerText: "Race", field: "raceName", resizable: "enabled" },
            { headerText: "Circuit", field: "circuit", resizable: "enabled" },
            { headerText: "Date", field: "raceDate" },
            { headerText: "Team", field: "team" },
            { headerText: "Pos", field: "position" },
            { headerText: "Points", field: "points" },
            { headerText: "Created", field: "timestamp", converter: self.createdConverter }
        ];

        function emptyDriverAnalytics(driverName) {
            return {
                driver: driverName || "",
                totalPoints: 0,
                wins: 0,
                podiums: 0,
                races: 0,
                avgFinish: 0,
                pointsByRace: [],
                finishByRace: [],
                teamPoints: [],
                pointsBySeason: [],
                podiumWinBreakdown: [],
                lapTrend: [],
                stintSummary: [],
                pitStopStats: [],
                weatherSummary: [],
                telemetrySummary: [],
                raceControlTimeline: [],
                raceResults: []
            };
        }

        function safeNumber(value) {
            return Number(value) || 0;
        }

        function pointsLabel(value) {
            const points = safeNumber(value);
            return Number.isInteger(points) ? String(points) : points.toFixed(1);
        }

        function compactLabel(value, maxLen) {
            const text = String(value || "");
            if (text.length <= maxLen) {
                return text;
            }
            return text.substring(0, Math.max(0, maxLen - 3)) + "...";
        }

        function selectedSeasonNumber() {
            const value = self.selectedSeason();
            if (value === ALL_SEASONS_VALUE || value === null || value === undefined || value === "") {
                return null;
            }
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : null;
        }

        function raceGroup(row) {
            const round = safeNumber(row && row.round);
            const season = safeNumber(row && row.season);
            if (selectedSeasonNumber() !== null && round > 0) {
                return "R" + round;
            }
            if (season > 0 && round > 0) {
                return season + " R" + round;
            }
            return compactLabel(row && row.label, 14);
        }

        function normalizeAnalytics(payload) {
            const base = payload || {};
            return {
                driver: base.driver || self.selectedDriver() || "",
                totalPoints: safeNumber(base.totalPoints),
                wins: safeNumber(base.wins),
                podiums: safeNumber(base.podiums),
                races: safeNumber(base.races),
                avgFinish: safeNumber(base.avgFinish),
                pointsByRace: Array.isArray(base.pointsByRace) ? base.pointsByRace : [],
                finishByRace: Array.isArray(base.finishByRace) ? base.finishByRace : [],
                teamPoints: Array.isArray(base.teamPoints) ? base.teamPoints : [],
                pointsBySeason: Array.isArray(base.pointsBySeason) ? base.pointsBySeason : [],
                podiumWinBreakdown: Array.isArray(base.podiumWinBreakdown) ? base.podiumWinBreakdown : [],
                lapTrend: Array.isArray(base.lapTrend) ? base.lapTrend : [],
                stintSummary: Array.isArray(base.stintSummary) ? base.stintSummary : [],
                pitStopStats: Array.isArray(base.pitStopStats) ? base.pitStopStats : [],
                weatherSummary: Array.isArray(base.weatherSummary) ? base.weatherSummary : [],
                telemetrySummary: Array.isArray(base.telemetrySummary) ? base.telemetrySummary : [],
                raceControlTimeline: Array.isArray(base.raceControlTimeline) ? base.raceControlTimeline : [],
                raceResults: Array.isArray(base.raceResults) ? base.raceResults : []
            };
        }

        function normalizeResult(row, index) {
            const normalized = Object.assign({}, row);
            normalized.rowKey = (normalized.id || "result") + "-" + index;
            normalized.season = safeNumber(normalized.season);
            normalized.round = safeNumber(normalized.round);
            normalized.position = safeNumber(normalized.position);
            normalized.points = safeNumber(normalized.points);
            normalized.timestamp = new Date(safeNumber(normalized.timestamp) || Date.now());
            return normalized;
        }

        function providerFromRows(rows) {
            return new ArrayDataProvider(rows, { keyAttributes: "id" });
        }

        self.driverTitle = ko.pureComputed(function () {
            return self.analytics().driver || self.selectedDriver() || "Driver Focus";
        });

        self.scopeLabel = ko.pureComputed(function () {
            const season = selectedSeasonNumber();
            return season === null ? "Scope: All Seasons" : "Scope: Season " + season;
        });

        self.totalPointsDisplay = ko.pureComputed(function () {
            return pointsLabel(self.analytics().totalPoints);
        });

        self.winsDisplay = ko.pureComputed(function () {
            return String(safeNumber(self.analytics().wins));
        });

        self.podiumsDisplay = ko.pureComputed(function () {
            return String(safeNumber(self.analytics().podiums));
        });

        self.racesDisplay = ko.pureComputed(function () {
            return String(safeNumber(self.analytics().races));
        });

        self.avgFinishDisplay = ko.pureComputed(function () {
            const avg = safeNumber(self.analytics().avgFinish);
            return avg > 0 ? avg.toFixed(2) : "0.00";
        });

        self.piePointsLabel = function (context) {
            return pointsLabel(context && context.value) + " pts";
        };

        self.pieCountLabel = function (context) {
            return String(safeNumber(context && context.value));
        };

        self.pointsByRaceDp = ko.pureComputed(function () {
            const rows = (self.analytics().pointsByRace || []).map(function (row, index) {
                const value = safeNumber(row.points);
                return {
                    id: "pbr-" + index,
                    series: "Points",
                    group: raceGroup(row),
                    value: value,
                    shortDesc: (row.label || raceGroup(row)) + " - " + pointsLabel(value) + " pts"
                };
            });
            return providerFromRows(rows);
        });

        self.finishByRaceDp = ko.pureComputed(function () {
            const rows = (self.analytics().finishByRace || []).map(function (row, index) {
                const value = safeNumber(row.position);
                return {
                    id: "fbr-" + index,
                    series: "Finish Position",
                    group: raceGroup(row),
                    value: value,
                    shortDesc: (row.label || raceGroup(row)) + " - P" + value
                };
            });
            return providerFromRows(rows);
        });

        self.teamPointsDp = ko.pureComputed(function () {
            const rows = (self.analytics().teamPoints || []).map(function (row, index) {
                const value = safeNumber(row.points);
                return {
                    id: "tp-" + index,
                    series: compactLabel(row.team, 20),
                    group: "Team Points",
                    value: value,
                    shortDesc: row.team + " - " + pointsLabel(value) + " pts"
                };
            });
            return providerFromRows(rows);
        });

        self.pointsBySeasonDp = ko.pureComputed(function () {
            const rows = (self.analytics().pointsBySeason || []).map(function (row, index) {
                const value = safeNumber(row.points);
                return {
                    id: "pbs-" + index,
                    series: "Points by Season",
                    group: String(row.season),
                    value: value,
                    shortDesc: row.season + " - " + pointsLabel(value) + " pts"
                };
            });
            return providerFromRows(rows);
        });

        self.pointsByRoundDp = ko.pureComputed(function () {
            const byRound = new Map();
            (self.analytics().pointsByRace || []).forEach(function (row) {
                const round = safeNumber(row && row.round);
                if (!round) {
                    return;
                }
                const existing = byRound.get(round) || 0;
                byRound.set(round, existing + safeNumber(row.points));
            });

            const rows = Array.from(byRound.entries())
                .sort(function (a, b) {
                    return a[0] - b[0];
                })
                .map(function (entry, index) {
                    const round = entry[0];
                    const value = entry[1];
                    return {
                        id: "pbrnd-" + index,
                        series: "Points by Round",
                        group: "R" + round,
                        value: value,
                        shortDesc: "Round " + round + " - " + pointsLabel(value) + " pts"
                    };
                });
            return providerFromRows(rows);
        });

        self.chart4Title = ko.pureComputed(function () {
            return selectedSeasonNumber() === null ? "Points by Season" : "Points by Round";
        });

        self.chart4Kpi = ko.pureComputed(function () {
            if (selectedSeasonNumber() === null) {
                return "Season totals for the selected driver";
            }
            return "Round-by-round totals for the selected season";
        });

        self.chart4Dp = ko.pureComputed(function () {
            return selectedSeasonNumber() === null ? self.pointsBySeasonDp() : self.pointsByRoundDp();
        });

        self.chart4XAxis = ko.pureComputed(function () {
            if (selectedSeasonNumber() === null) {
                return {
                    title: "Season",
                    titleStyle: "font-size:12px;color:#eef4ff;",
                    tickLabel: { style: "font-size:11px;color:#eef4ff;" }
                };
            }
            return {
                title: "Round",
                titleStyle: "font-size:12px;color:#eef4ff;",
                tickLabel: { style: "font-size:11px;color:#eef4ff;" }
            };
        });

        self.chart4YAxis = ko.pureComputed(function () {
            return {
                title: "Points",
                titleStyle: "font-size:12px;color:#eef4ff;",
                tickLabel: { style: "font-size:11px;color:#eef4ff;" }
            };
        });

        self.breakdownDp = ko.pureComputed(function () {
            const rows = (self.analytics().podiumWinBreakdown || []).map(function (row, index) {
                const value = safeNumber(row.count);
                return {
                    id: "pwb-" + index,
                    series: compactLabel(row.bucket, 22),
                    group: "Race Outcomes",
                    value: value,
                    shortDesc: row.bucket + " - " + value
                };
            });
            return providerFromRows(rows);
        });

        self.lapTrendDp = ko.pureComputed(function () {
            const rows = (self.analytics().lapTrend || []).map(function (row, index) {
                const label = row.label || ("R" + safeNumber(row.round));
                const value = safeNumber(row.avgLapTimeSeconds);
                return {
                    id: "lap-" + index,
                    series: "Avg Lap Time",
                    group: label,
                    value: value,
                    shortDesc: label + " - " + value.toFixed(3) + "s"
                };
            });
            return providerFromRows(rows);
        });

        self.stintSummaryDp = ko.pureComputed(function () {
            const rows = (self.analytics().stintSummary || []).map(function (row, index) {
                const compound = String(row.compound || "Unknown");
                const value = safeNumber(row.laps);
                return {
                    id: "stint-" + index,
                    series: "Stint Laps",
                    group: compactLabel(compound, 16),
                    value: value,
                    shortDesc: compound + " - " + value + " laps"
                };
            });
            return providerFromRows(rows);
        });

        self.pitStopDp = ko.pureComputed(function () {
            const rows = (self.analytics().pitStopStats || []).map(function (row, index) {
                const label = String(row.label || "Race");
                const value = safeNumber(row.avgDurationSeconds);
                const stops = safeNumber(row.stops);
                return {
                    id: "pit-" + index,
                    series: "Avg Pit Duration",
                    group: compactLabel(label, 16),
                    value: value,
                    shortDesc: label + " - " + value.toFixed(3) + "s avg over " + stops + " stop(s)"
                };
            });
            return providerFromRows(rows);
        });

        self.weatherDp = ko.pureComputed(function () {
            const rows = [];
            (self.analytics().weatherSummary || []).forEach(function (row, index) {
                const label = String(row.label || "Race");
                const air = safeNumber(row.avgAirTemp);
                const track = safeNumber(row.avgTrackTemp);
                rows.push({
                    id: "weather-air-" + index,
                    series: "Air Temp",
                    group: compactLabel(label, 18),
                    value: air,
                    shortDesc: label + " - Air " + air.toFixed(1) + " C"
                });
                rows.push({
                    id: "weather-track-" + index,
                    series: "Track Temp",
                    group: compactLabel(label, 18),
                    value: track,
                    shortDesc: label + " - Track " + track.toFixed(1) + " C"
                });
            });
            return providerFromRows(rows);
        });

        self.telemetryDp = ko.pureComputed(function () {
            const rows = [];
            (self.analytics().telemetrySummary || []).forEach(function (row, index) {
                const label = String(row.label || "Race");
                const max = safeNumber(row.maxSpeed);
                const avg = safeNumber(row.avgSpeed);
                rows.push({
                    id: "telemetry-max-" + index,
                    series: "Max Speed",
                    group: compactLabel(label, 18),
                    value: max,
                    shortDesc: label + " - Max " + max.toFixed(1) + " km/h"
                });
                rows.push({
                    id: "telemetry-avg-" + index,
                    series: "Avg Speed",
                    group: compactLabel(label, 18),
                    value: avg,
                    shortDesc: label + " - Avg " + avg.toFixed(1) + " km/h"
                });
            });
            return providerFromRows(rows);
        });

        self.raceControlDp = ko.pureComputed(function () {
            const counts = new Map();
            (self.analytics().raceControlTimeline || []).forEach(function (entry) {
                const category = compactLabel(entry.category || "Unknown", 16);
                counts.set(category, (counts.get(category) || 0) + 1);
            });
            const rows = Array.from(counts.entries())
                .sort(function (a, b) {
                    return b[1] - a[1];
                })
                .map(function (entry, index) {
                    return {
                        id: "rc-" + index,
                        series: "Race Control",
                        group: entry[0],
                        value: entry[1],
                        shortDesc: entry[0] + " - " + entry[1] + " message(s)"
                    };
                });
            return providerFromRows(rows);
        });

        self.raceControlPreview = ko.pureComputed(function () {
            return (self.analytics().raceControlTimeline || []).slice(0, 6);
        });

        self.domainAvailability = ko.pureComputed(function () {
            const analytics = self.analytics();
            const blocks = [
                analytics.lapTrend,
                analytics.stintSummary,
                analytics.pitStopStats,
                analytics.weatherSummary,
                analytics.telemetrySummary,
                analytics.raceControlTimeline
            ];
            const available = blocks.filter(function (rows) {
                return Array.isArray(rows) && rows.length > 0;
            }).length;
            return "Domain feeds available: " + available + "/6";
        });

        function fetchJson(url, errorText) {
            return fetch(url)
                .then(function (res) {
                    if (res.ok) {
                        return res.json();
                    }
                    return res.text().then(function (txt) {
                        return Promise.reject(txt || errorText);
                    });
                });
        }

        function loadDrivers() {
            return fetchJson("/f1/drivers", "Unable to load drivers")
                .then(function (names) {
                    return Array.isArray(names) ? names : [];
                });
        }

        function loadSeasons() {
            return fetchJson("/f1/seasons", "Unable to load seasons")
                .then(function (seasons) {
                    return (Array.isArray(seasons) ? seasons : [])
                        .map(function (season) {
                            return safeNumber(season);
                        })
                        .filter(function (season) {
                            return season >= 2010;
                        });
                });
        }

        function loadDriverAnalytics(driverName) {
            const requested = String(driverName || "").trim();
            if (!requested) {
                self.analytics(emptyDriverAnalytics(""));
                self.raceResults([]);
                return Promise.resolve();
            }

            const paramsQuery = new URLSearchParams();
            paramsQuery.set("name", requested);
            const season = selectedSeasonNumber();
            if (season !== null) {
                paramsQuery.set("season", String(season));
            }

            self.loading(true);
            self.errorMessage("");
            return fetch("/f1/analytics/driver?" + paramsQuery.toString())
                .then(function (res) {
                    if (res.ok) {
                        return res.json();
                    }
                    return res.text().then(function (txt) {
                        return Promise.reject(txt || "Unable to load driver analytics");
                    });
                })
                .then(function (payload) {
                    const normalized = normalizeAnalytics(payload);
                    self.analytics(normalized);
                    self.raceResults(normalized.raceResults.map(normalizeResult));
                })
                .catch(function (err) {
                    self.analytics(emptyDriverAnalytics(requested));
                    self.raceResults([]);
                    self.errorMessage(String(err || "Unable to load driver analytics"));
                })
                .finally(function () {
                    self.loading(false);
                });
        }

        function initializeSelections() {
            return Promise.all([loadDrivers(), loadSeasons()])
                .then(function (result) {
                    const drivers = result[0];
                    const seasons = result[1];

                    self.driverOptions(drivers.map(function (name) {
                        return { value: name, label: name };
                    }));

                    const seasonRows = [{ value: ALL_SEASONS_VALUE, label: "All Seasons" }]
                        .concat(seasons.map(function (season) {
                            const value = String(season);
                            return { value: value, label: value };
                        }));
                    self.seasonOptions(seasonRows);

                    suppressAutoRefresh = true;
                    if (drivers.length > 0) {
                        self.selectedDriver(drivers[0]);
                    }
                    if (seasons.length > 0) {
                        self.selectedSeason(String(seasons[0]));
                    } else {
                        self.selectedSeason(ALL_SEASONS_VALUE);
                    }
                    suppressAutoRefresh = false;

                    if (drivers.length > 0) {
                        return loadDriverAnalytics(drivers[0]);
                    }
                    self.analytics(emptyDriverAnalytics(""));
                    self.raceResults([]);
                    return Promise.resolve();
                });
        }

        self.refreshDriverAnalytics = function () {
            const selected = String(self.selectedDriver() || "").trim();
            if (!selected) {
                initializeSelections().catch(function (err) {
                    self.errorMessage(String(err || "Unable to initialize driver page"));
                });
                return;
            }
            loadDriverAnalytics(selected);
        };

        self.goDashboard = function () {
            const rootRouter = params && params.root && params.root.router ? params.root.router : Router.rootInstance;
            if (rootRouter && typeof rootRouter.go === "function") {
                rootRouter.go("dashboard");
            }
        };

        self.selectedDriver.subscribe(function () {
            if (suppressAutoRefresh) {
                return;
            }
            self.refreshDriverAnalytics();
        });

        self.selectedSeason.subscribe(function () {
            if (suppressAutoRefresh) {
                return;
            }
            self.refreshDriverAnalytics();
        });

        initializeSelections().catch(function (err) {
            self.errorMessage(String(err || "Unable to initialize driver page"));
        });
    };
});

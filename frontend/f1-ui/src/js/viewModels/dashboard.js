define([
    "knockout",
    "ojs/ojarraydataprovider",
    "ojs/ojconverter-datetime",
    "ojs/ojrouter",
    "ojs/ojtable",
    "ojs/ojdrawerpopup",
    "ojs/ojinputtext",
    "ojs/ojbutton",
    "ojs/ojchart"
], function (ko, ArrayDataProvider, DateTimeConverter, Router) {

    return function DashboardVM(params) {
        const self = this;
        const MIN_SEASON = 2010;
        const MAX_SEASON = 2100;

        self.results = ko.observableArray([]);
        self.driverStandings = ko.observableArray([]);
        self.constructorStandings = ko.observableArray([]);
        self.resultsDrawerOpen = ko.observable(false);
        self.syncSeason = ko.observable(new Date().getFullYear());
        self.availableSeasons = ko.observableArray([]);
        self.analytics = ko.observable(emptyAnalytics(self.syncSeason()));

        self.dp = new ArrayDataProvider(self.results, { keyAttributes: "id" });

        function emptyAnalytics(season) {
            return {
                season: Number(season) || new Date().getFullYear(),
                driverTop10: [],
                constructorTop10: [],
                teamPoints: [],
                raceWinners: []
            };
        }

        function safeNumber(value) {
            return Number(value) || 0;
        }

        function pointsLabel(value) {
            const points = safeNumber(value);
            return Number.isInteger(points) ? String(points) : points.toFixed(1);
        }

        self.piePointsLabel = function (context) {
            return pointsLabel(context && context.value) + " pts";
        };

        function compactLabel(value, maxLen) {
            const text = String(value || "");
            if (text.length <= maxLen) {
                return text;
            }
            return text.substring(0, Math.max(0, maxLen - 3)) + "...";
        }

        function rankedLabel(entry, maxLen) {
            const rank = safeNumber(entry && entry.rank);
            const prefix = rank > 0 ? rank + ". " : "";
            return compactLabel(prefix + (entry && entry.name ? entry.name : ""), maxLen);
        }

        function providerFromRows(rows) {
            return new ArrayDataProvider(rows, { keyAttributes: "id" });
        }

        function normalizeSeasonAnalytics(payload, season) {
            const base = payload || {};
            const normalized = emptyAnalytics(season);
            normalized.season = safeNumber(base.season) || normalized.season;
            normalized.driverTop10 = Array.isArray(base.driverTop10) ? base.driverTop10 : [];
            normalized.constructorTop10 = Array.isArray(base.constructorTop10) ? base.constructorTop10 : [];
            normalized.teamPoints = Array.isArray(base.teamPoints) ? base.teamPoints : [];
            normalized.raceWinners = Array.isArray(base.raceWinners) ? base.raceWinners : [];
            return normalized;
        }

        self.driverChartDp = ko.pureComputed(function () {
            const rows = (self.analytics().driverTop10 || [])
                .slice(0, 10)
                .map(function (row, index) {
                    const fullName = row.rank + ". " + row.name;
                    const points = safeNumber(row.points);
                    return {
                        id: "driver-" + index,
                        series: rankedLabel(row, 20),
                        group: "Driver Points",
                        value: points,
                        shortDesc: fullName + " - " + pointsLabel(points) + " pts"
                    };
                });
            return providerFromRows(rows);
        });

        self.constructorChartDp = ko.pureComputed(function () {
            const rows = (self.analytics().constructorTop10 || [])
                .slice(0, 10)
                .map(function (row, index) {
                    const fullName = row.rank + ". " + row.name;
                    const points = safeNumber(row.points);
                    return {
                        id: "constructor-" + index,
                        series: rankedLabel(row, 20),
                        group: "Constructor Points",
                        value: points,
                        shortDesc: fullName + " - " + pointsLabel(points) + " pts"
                    };
                });
            return providerFromRows(rows);
        });

        self.teamPointsChartDp = ko.pureComputed(function () {
            const rows = (self.analytics().teamPoints || [])
                .slice(0, 10)
                .map(function (row, index) {
                    const points = safeNumber(row.points);
                    return {
                        id: "team-" + index,
                        series: "Team Points",
                        group: compactLabel(row.team, 14),
                        value: points,
                        shortDesc: row.team + " - " + pointsLabel(points) + " pts"
                    };
                });
            return providerFromRows(rows);
        });

        self.winnersChartDp = ko.pureComputed(function () {
            const rows = (self.analytics().raceWinners || [])
                .slice(0, 10)
                .map(function (row, index) {
                    const wins = safeNumber(row.wins);
                    return {
                        id: "winner-" + index,
                        series: "Race Wins",
                        group: compactLabel(row.driver, 14),
                        value: wins,
                        shortDesc: row.driver + " - " + wins + " win(s)"
                    };
                });
            return providerFromRows(rows);
        });

        self.driverHeadline = ko.pureComputed(function () {
            const leader = (self.analytics().driverTop10 || [])[0];
            if (!leader) {
                return "No driver data in selected season";
            }
            return "Leader: " + leader.name + " (" + pointsLabel(leader.points) + " pts)";
        });

        self.constructorHeadline = ko.pureComputed(function () {
            const leader = (self.analytics().constructorTop10 || [])[0];
            if (!leader) {
                return "No constructor data in selected season";
            }
            return "Leader: " + leader.name + " (" + pointsLabel(leader.points) + " pts)";
        });

        self.teamHeadline = ko.pureComputed(function () {
            const teams = self.analytics().teamPoints || [];
            return "Teams tracked: " + teams.length;
        });

        self.winnerHeadline = ko.pureComputed(function () {
            const top = (self.analytics().raceWinners || [])[0];
            if (!top) {
                return "No race winner data";
            }
            return "Most wins: " + top.driver + " (" + top.wins + ")";
        });

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
            { headerText: "Driver", field: "driver" },
            { headerText: "Team", field: "team" },
            { headerText: "Pos", field: "position" },
            { headerText: "Points", field: "points" },
            { headerText: "Created", field: "timestamp", converter: self.createdConverter }
        ];

        function normalizeRow(row) {
            const normalized = Object.assign({}, row);
            normalized.season = Number(normalized.season) || 0;
            normalized.round = Number(normalized.round) || 0;
            normalized.position = Number(normalized.position) || 0;
            normalized.points = Number(normalized.points) || 0;
            normalized.timestamp = new Date(Number(normalized.timestamp) || Date.now());
            return normalized;
        }

        function loadResults() {
            return fetch("/f1/results")
                .then(function (res) {
                    return res.ok ? res.json() : Promise.reject("Unable to load race results");
                })
                .then(function (data) {
                    self.results((data || []).map(normalizeRow));
                });
        }

        function loadDriverStandings() {
            return fetch("/f1/standings/drivers")
                .then(function (res) {
                    return res.ok ? res.json() : Promise.reject("Unable to load driver standings");
                })
                .then(function (data) {
                    self.driverStandings(data || []);
                });
        }

        function loadConstructorStandings() {
            return fetch("/f1/standings/constructors")
                .then(function (res) {
                    return res.ok ? res.json() : Promise.reject("Unable to load constructor standings");
                })
                .then(function (data) {
                    self.constructorStandings(data || []);
                });
        }

        function loadAvailableSeasons() {
            return fetch("/f1/seasons")
                .then(function (res) {
                    return res.ok ? res.json() : [];
                })
                .then(function (data) {
                    const seasons = (data || [])
                        .map(function (value) {
                            return Number(value);
                        })
                        .filter(function (season) {
                            return season >= MIN_SEASON && season <= MAX_SEASON;
                        })
                        .sort(function (a, b) {
                            return b - a;
                        });

                    self.availableSeasons(seasons);

                    if (seasons.length) {
                        const selected = Number(self.syncSeason());
                        if (!selected || seasons.indexOf(selected) === -1) {
                            self.syncSeason(seasons[0]);
                        }
                    }
                })
                .catch(function () {
                    self.availableSeasons([]);
                });
        }

        const seasonSyncAttempts = new Set();

        function hasChartData(payload) {
            const analytics = payload || {};
            return Boolean(
                (analytics.driverTop10 && analytics.driverTop10.length) ||
                (analytics.constructorTop10 && analytics.constructorTop10.length) ||
                (analytics.teamPoints && analytics.teamPoints.length) ||
                (analytics.raceWinners && analytics.raceWinners.length)
            );
        }

        function ensureSeasonData(season) {
            if (!season || season < MIN_SEASON || season > MAX_SEASON) {
                return Promise.resolve();
            }
            if ((self.availableSeasons() || []).indexOf(season) >= 0) {
                return Promise.resolve();
            }
            if (seasonSyncAttempts.has(season)) {
                return Promise.resolve();
            }

            seasonSyncAttempts.add(season);

            return syncByPath("/f1/sync/" + season, true, function (payload) {
                const degraded = Boolean(payload && payload.degraded);
                const imported = Number(payload && payload.imported) || 0;
                if (!degraded || imported > 0) {
                    const seasons = (self.availableSeasons() || []).slice(0);
                    if (seasons.indexOf(season) < 0) {
                        seasons.push(season);
                        seasons.sort(function (a, b) {
                            return b - a;
                        });
                        self.availableSeasons(seasons);
                    }
                }
            });
        }

        function loadAnalyticsForSeason(season) {
            const resolvedSeason = Number(season);
            if (!resolvedSeason || resolvedSeason < MIN_SEASON || resolvedSeason > MAX_SEASON) {
                self.analytics(emptyAnalytics(season));
                return Promise.resolve();
            }

            return ensureSeasonData(resolvedSeason)
                .then(function () {
                    return fetch("/f1/analytics/" + resolvedSeason);
                })
                .then(function (res) {
                    return res.ok ? res.json() : Promise.reject("Unable to load season analytics");
                })
                .then(function (payload) {
                    const normalized = normalizeSeasonAnalytics(payload, resolvedSeason);
                    self.analytics(normalized);

                    if (hasChartData(normalized)) {
                        const seasons = (self.availableSeasons() || []).slice(0);
                        if (seasons.indexOf(resolvedSeason) < 0) {
                            seasons.push(resolvedSeason);
                            seasons.sort(function (a, b) {
                                return b - a;
                            });
                            self.availableSeasons(seasons);
                        }
                    }
                });
        }

        self.refreshDashboard = function () {
            Promise.all([
                loadResults(),
                loadDriverStandings(),
                loadConstructorStandings(),
                loadAvailableSeasons()
            ])
                .then(function () {
                    return loadAnalyticsForSeason(Number(self.syncSeason()));
                })
                .catch(function (err) {
                    console.error(err);
                });
        };

        function syncByPath(path, silent, afterSync) {
            return fetch(path, { method: "POST" })
                .then(function (res) {
                    if (res.ok) {
                        return res.json();
                    }
                    return res.text().then(function (txt) {
                        return Promise.reject(txt || "Sync failed");
                    });
                })
                .then(function (payload) {
                    if (typeof afterSync === "function") {
                        afterSync(payload);
                    }
                    self.refreshDashboard();
                    if (!silent) {
                        const count = Number(payload && payload.imported ? payload.imported : 0);
                        alert("Live sync completed. Imported rows: " + count);
                    }
                })
                .catch(function (err) {
                    const message = String(err || "Sync failed");
                    const pythonHint =
                        "FastF1 sync is unavailable right now. Ensure Python 3 is installed and run " +
                        "`python -m pip install -r scripts/fastf1-ingest/requirements.txt`.";
                    const lowerMessage = message.toLowerCase();
                    const pythonRelated =
                        lowerMessage.indexOf("python") >= 0 ||
                        lowerMessage.indexOf("modulenotfounderror") >= 0 ||
                        lowerMessage.indexOf("no module named") >= 0 ||
                        lowerMessage.indexOf("fastf1") >= 0 ||
                        lowerMessage.indexOf("pip install") >= 0;
                    if (!silent) {
                        if (pythonRelated) {
                            alert(pythonHint);
                        } else {
                            alert("Sync failed: " + message);
                        }
                    } else if (!pythonRelated) {
                        console.error("Auto-sync failed:", message);
                    }
                });
        }

        self.syncCurrentSeason = function () {
            syncByPath("/f1/sync/current", false, function (payload) {
                const season = Number(payload && payload.season);
                if (season >= MIN_SEASON && season <= MAX_SEASON) {
                    self.syncSeason(season);
                }
            });
        };

        self.syncSelectedSeason = function () {
            const season = Number(self.syncSeason());
            if (!season || season < MIN_SEASON || season > MAX_SEASON) {
                alert("Enter a valid season year.");
                return;
            }
            syncByPath("/f1/sync/" + season, false);
        };

        self.showRaceResults = function () {
            self.resultsDrawerOpen(true);
        };

        self.goDriverFocus = function () {
            const rootRouter = params && params.root && params.root.router ? params.root.router : Router.rootInstance;
            if (rootRouter && typeof rootRouter.go === "function") {
                rootRouter.go("driver");
            }
        };

        self.closeRaceResults = function () {
            self.resultsDrawerOpen(false);
        };

        self.syncSeason.subscribe(function (season) {
            const parsed = Number(season);
            if (!parsed || parsed < MIN_SEASON || parsed > MAX_SEASON) {
                return;
            }
            loadAnalyticsForSeason(parsed).catch(function (err) {
                console.error(err);
            });
        });

        self.refreshDashboard();
    };
});

define([
    "ojs/ojcontext",
    "ojs/ojresponsiveutils",
    "ojs/ojresponsiveknockoututils",
    "knockout",
    "ojs/ojrouter",
    "ojs/ojmodule-element",
    "ojs/ojmodule-element-utils",
    "ojs/ojknockout"
], function (Context, ResponsiveUtils, ResponsiveKnockoutUtils, ko, Router, Module, ModuleUtils) {

    function ControllerViewModel() {
        const self = this;

        const smQuery = ResponsiveUtils.getFrameworkQuery(ResponsiveUtils.FRAMEWORK_QUERY_KEY.SM_ONLY);
        self.smScreen = ResponsiveKnockoutUtils.createMediaQueryObservable(smQuery);

        self.appName = ko.observable("Formula 1 Analytics Dashboard");
        self.userLogin = ko.observable("");
        self.userLogin(sessionStorage.getItem("userName") || "");

        self.footerLinks = [
            { name: "Formula 1", linkTarget: "https://www.formula1.com/" },
            { name: "F1 Standings", linkTarget: "https://www.formula1.com/en/results" }
        ];

        self.router = Router.rootInstance;
        self.router.configure({
            login: { label: "Login", isDefault: true },
            dashboard: { label: "Dashboard" },
            driver: { label: "Driver Focus" }
        });

        self.router.urlAdapter = new Router.urlParamAdapter();
        self.selection = self.router.currentState;

        self.showUserInfo = ko.pureComputed(function () {
            const st = self.selection();
            return st && (st.id === "dashboard" || st.id === "driver");
        });

        self.moduleConfig = ko.observable({ view: [], viewModel: null });

        function loadModule(state) {
            if (!state || !state.id) {
                return;
            }

            const viewPath = "views/" + state.id + ".html";
            const modelPath = "viewModels/" + state.id;

            Promise.all([
                ModuleUtils.createView({ viewPath: viewPath }),
                ModuleUtils.createViewModel({ viewModelPath: modelPath, params: { root: self } })
            ]).then(function (parts) {
                self.moduleConfig({ view: parts[0], viewModel: parts[1] });
            }).catch(function (err) {
                console.error("Failed to load module", state.id, err);
            });
        }

        self.router.currentState.subscribe(loadModule);

        Router.sync().then(function () {
            loadModule(self.router.currentState());

            const loggedIn = !!sessionStorage.getItem("userName");
            const wanted = loggedIn ? "dashboard" : "login";
            if (self.router.stateId() !== wanted) {
                self.router.go(wanted);
            }
        });

        self.menuAction = function () {
            sessionStorage.removeItem("userName");
            self.userLogin("");
            Router.rootInstance.go("login");
        };
    }

    Context.getPageContext().getBusyContext().applicationBootstrapComplete();
    return new ControllerViewModel();
});

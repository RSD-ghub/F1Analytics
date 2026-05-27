define(["knockout", "ojs/ojrouter", "ojs/ojinputtext", "ojs/ojbutton"], function (ko, Router) {
    function LoginVM(params) {
        const self = this;
        const root = params.root;

        self.userName = ko.observable("");

        self.login = function () {
            const val = self.userName().trim();
            if (!val) {
                alert("Enter your name");
                return;
            }

            sessionStorage.setItem("userName", val);
            root.userLogin(val);
            Router.rootInstance.go("dashboard");
        };
    }

    return LoginVM;
});
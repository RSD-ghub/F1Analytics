/**
 * @license
 * Copyright (c) 2014, 2025, Oracle and/or its affiliates.
 * Licensed under The Universal Permissive License (UPL), Version 1.0
 * as shown at https://oss.oracle.com/licenses/upl/
 * @ignore
 */
require(["ojs/ojbootstrap","knockout","./appController","ojs/ojknockout","ojs/ojbutton","ojs/ojtoolbar","ojs/ojmenu"],(function(o,n,t){o.whenDocumentReady().then((function(){function o(){n.applyBindings(t,document.getElementById("globalBody"))}document.body.classList.contains("oj-hybrid")?document.addEventListener("deviceready",o):o()}))}));
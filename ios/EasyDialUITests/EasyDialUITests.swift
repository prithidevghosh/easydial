import XCTest

final class EasyDialUITests: XCTestCase {
    @MainActor
    func testLiveHomeAndConnectionSettings() throws {
        let app = XCUIApplication()
        app.launch()
        let settings = app.buttons["Connection settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["Connect your stores to get started"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts["Sample mode. No orders or payments."].exists)
        let home = XCTAttachment(screenshot: app.screenshot())
        home.name = "Live home — disconnected accounts"
        home.lifetime = .keepAlways
        add(home)
        settings.tap()
        XCTAssertTrue(app.staticTexts["Blinkit, Needs connection"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Commerce, Live"].exists)
    }
}

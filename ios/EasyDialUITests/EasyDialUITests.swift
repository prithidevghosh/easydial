import XCTest

final class EasyDialUITests: XCTestCase {
    @MainActor
    func testSampleBasketJourney() throws {
        let app = XCUIApplication()
        app.launch()
        let milk = app.buttons["searchMilk"]
        XCTAssertTrue(milk.waitForExistence(timeout: 15), "Start the sample backend on port 8000 before UI tests")
        let home = XCTAttachment(screenshot: app.screenshot())
        home.name = "EasyDial home"
        home.lifetime = .keepAlways
        self.add(home)
        milk.tap()
        let add = app.buttons["add-sample:blinkit:milk-amul"]
        XCTAssertTrue(add.waitForExistence(timeout: 10))
        let choices = XCTAttachment(screenshot: app.screenshot())
        choices.name = "EasyDial product choices"
        choices.lifetime = .keepAlways
        self.add(choices)
        for _ in 0..<4 where !add.isHittable { app.swipeUp() }
        add.tap()
        let basket = app.buttons["basketShortcut"]
        XCTAssertTrue(basket.waitForExistence(timeout: 5))
        basket.tap()
        let review = app.buttons["reviewBasket"]
        for _ in 0..<5 where !review.isHittable { app.swipeUp() }
        XCTAssertTrue(review.isHittable)
        review.tap()
        let complete = app.buttons["completeSample"]
        XCTAssertTrue(complete.waitForExistence(timeout: 5))
        for _ in 0..<4 where !complete.isHittable { app.swipeUp() }
        complete.tap()
        app.buttons["Confirm sample checkout"].tap()
        XCTAssertTrue(app.staticTexts["No real order was placed."].waitForExistence(timeout: 5))
    }
}

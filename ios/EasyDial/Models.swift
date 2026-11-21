import Foundation

struct AppConfig: Decodable {
    let mode: String
    let voiceAvailable: Bool
    let conversationAvailable: Bool
    let liveCheckout: Bool
}

struct Offer: Decodable, Identifiable {
    let id: String
    let provider: String
    let name: String
    let brand: String?
    let variant: String?
    let packSize: String?
    let pricePaise: Int?
    let available: Bool?
    let maxQuantity: Int
    let mode: String
    let suggestedQuantity: Int
    let replacesLineId: String?
    var store: String { provider == "instamart" ? "Swiggy Instamart" : "Blinkit" }
}

struct SearchResult: Decodable {
    let provider: String
    let query: String
    let offers: [Offer]
    let status: String
    let message: String?
    let truncated: Bool
}

struct BasketLine: Decodable, Identifiable {
    let id: String
    let offer: Offer
    let quantity: Int
}

struct ConversationMessage: Decodable {
    let role: String
    let text: String
}

struct BasketReview: Decodable {
    let id: String
    let version: Int
    let provider: String
    let addressLabel: String
    let itemSubtotalPaise: Int
    let feesPaise: Int?
    let totalPaise: Int?
    let paymentMethod: String
    let mode: String
}

struct ShoppingSession: Decodable, Identifiable {
    let id: String
    let mode: String
    let version: Int
    let addressLabel: String
    let lines: [BasketLine]
    let results: [SearchResult]
    let messages: [ConversationMessage]
    let review: BasketReview?
    let phase: String
    let comparison: String?
    var subtotal: Int { lines.reduce(0) { $0 + ($1.offer.pricePaise ?? 0) * $1.quantity } }
    var itemCount: Int { lines.reduce(0) { $0 + $1.quantity } }
    var lastReply: String { messages.last(where: { $0.role == "assistant" })?.text ?? "What can I get for you?" }
}

struct SessionCreated: Decodable {
    let session: ShoppingSession
    let token: String
}

func rupees(_ paise: Int?) -> String {
    guard let paise else { return "Price unverified" }
    return (Decimal(paise) / 100).formatted(.currency(code: "INR").locale(Locale(identifier: "en_IN")))
}

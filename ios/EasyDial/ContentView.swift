import SwiftUI

private enum Palette {
    static let blue = Color(red: 0.075, green: 0.227, blue: 0.447)
    static let paper = Color(red: 0.973, green: 0.98, blue: 0.992)
    static let sky = Color(red: 0.91, green: 0.941, blue: 0.988)
    static let slate = Color(red: 0.32, green: 0.39, blue: 0.49)
    static let ink = Color(red: 0.094, green: 0.149, blue: 0.239)
}

// Own label contrast instead of inheriting the screen's foreground style.
private struct PrimaryActionStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(isEnabled ? Color.white : Palette.ink.opacity(0.55))
            .multilineTextAlignment(.center)
            .padding(.horizontal, 20).padding(.vertical, 17)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(isEnabled ? Palette.blue : Palette.sky, in: RoundedRectangle(cornerRadius: 18))
            .opacity(configuration.isPressed ? 0.82 : 1)
    }
}

private struct SoftActionStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Palette.blue.opacity(isEnabled ? 1 : 0.45))
            .padding(.horizontal, 18).padding(.vertical, 13)
            .frame(minHeight: 46)
            .background(Palette.sky.opacity(configuration.isPressed ? 1 : 0.65), in: RoundedRectangle(cornerRadius: 14))
    }
}

struct ContentView: View {
    @State private var model = ShoppingModel()
    @State private var settings = false
    @State private var showBasket = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        connectionStatus
                        if model.session == nil { connectionView } else {
                            if model.session?.results.isEmpty == true || model.voice.isRecording {
                                voiceSection
                            } else {
                                compactVoiceSection
                            }
                            if model.session?.results.isEmpty == false || !model.transcript.isEmpty || model.config?.conversationAvailable == true { assistantSection }
                            searchSection
                            resultsSection
                        }
                        if let error = model.error {
                            Label(error, systemImage: "exclamationmark.circle")
                                .foregroundStyle(.red).font(.body)
                                .accessibilityIdentifier("errorMessage")
                        }
                    }
                    .padding(24)
                    .padding(.bottom, 24)
                }
                .background(Palette.paper)
                .safeAreaInset(edge: .bottom) {
                    if let session = model.session, !session.lines.isEmpty || session.syncedProvider != nil {
                        Button {
                            showBasket = true
                        } label: {
                            HStack {
                                Label("Your basket · \(session.itemCount)", systemImage: "basket.fill")
                                Spacer()
                                Text(rupees(session.subtotal))
                            }
                            .font(.headline).padding(18)
                            .foregroundStyle(.white).background(Palette.blue, in: RoundedRectangle(cornerRadius: 18))
                        }
                        .padding(.horizontal, 20).padding(.bottom, 8)
                        .background(.regularMaterial)
                        .accessibilityIdentifier("basketShortcut")
                    }
                }
            }
            .navigationTitle("easydial")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Image(systemName: "phone.bubble.fill").foregroundStyle(Palette.blue)
                        .accessibilityHidden(true)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { settings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Connection settings")
                }
            }
            .sheet(isPresented: $settings) { settingsView }
            .sheet(isPresented: $showBasket) {
                NavigationStack {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 24) {
                            basketSection
                            reviewSection
                            if let error = model.error { Text(error).foregroundStyle(.red) }
                        }.padding(24)
                    }
                    .background(Palette.paper)
                    .navigationTitle("Your basket")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { showBasket = false } } }
                }
                .presentationDragIndicator(.visible)
            }
            .onChange(of: model.replaceLineID) { _, value in if value != nil { showBasket = false } }
            .task { await model.connect() }
            .onChange(of: scenePhase) { _, phase in
                if phase != .active { model.voice.cancel() }
            }
        }
        .tint(Palette.blue)
        .foregroundColor(Palette.ink)
    }

    private var connectionStatus: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 16) {
                storeStatus("Blinkit", connected: model.config?.providers["blinkit"] == true)
                storeStatus("Instamart", connected: model.config?.providers["instamart"] == true)
                Spacer(minLength: 0)
            }
            if model.config?.addressConfigured == true {
                Label(model.session?.addressLabel ?? "", systemImage: "location")
                    .font(.caption).foregroundStyle(Palette.slate).lineLimit(2)
            } else {
                Button("Connect your stores to get started") { settings = true }
                    .font(.subheadline.weight(.medium)).foregroundStyle(Palette.blue)
                    .frame(minHeight: 44)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.white, in: RoundedRectangle(cornerRadius: 18))
    }

    private func storeStatus(_ name: String, connected: Bool) -> some View {
        HStack(spacing: 6) {
            Circle().fill(connected ? Color.green : Color.secondary.opacity(0.5)).frame(width: 6, height: 6)
            Text(name).font(.subheadline.weight(.medium))
        }
        .accessibilityLabel("\(name), \(connected ? "configured; verify with search" : "not connected")")
    }

    private var connectionView: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Groceries start\nwith a conversation.")
                .font(.system(.largeTitle, design: .rounded, weight: .bold))
            Text("Speak naturally. We'll help you find what you need, one step at a time.")
                .font(.title3)
            if model.busy { ProgressView("Connecting to EasyDial…") }
            else {
                Button("Connect again") { Task { await model.connect() } }
                    .buttonStyle(PrimaryActionStyle())
            }
        }
        .padding(.vertical, 24)
    }

    private var voiceSection: some View {
        VStack(spacing: 20) {
            Text(model.voice.isRecording ? "I'm listening." : "Your everyday needs.\nJust a conversation.")
                .font(.system(.largeTitle, design: .rounded, weight: .medium))
                .tracking(-0.8)
                .multilineTextAlignment(.center)
            Text(model.voice.isRecording ? "Take your time. Tap again when you're done." : "Tell us what you need.\nWe’ll help you find it.")
                .font(.body).multilineTextAlignment(.center).foregroundStyle(Palette.slate)
            Button { Task { await model.toggleRecording() } } label: {
                ZStack {
                    Circle().stroke(Palette.blue.opacity(0.08), lineWidth: 1).frame(width: 202, height: 202)
                    Circle().fill(Palette.sky).frame(width: 176, height: 176)
                    Circle().fill(model.voice.isRecording ? Color.red : Palette.blue).frame(width: 138, height: 138)
                        .shadow(color: Palette.blue.opacity(0.16), radius: 14, y: 8)
                    Image(systemName: model.voice.isRecording ? "stop.fill" : "mic.fill")
                        .font(.system(size: 42, weight: .medium)).foregroundStyle(.white)
                }
            }
            .disabled(model.busy)
            .accessibilityLabel(model.voice.isRecording ? "Stop recording and send" : "Record grocery request")
            .accessibilityHint("Records for up to sixty seconds")
            Text(model.voice.isRecording ? "Recording · \(model.voice.seconds)s" : "Tap to speak")
                .font(.headline).foregroundStyle(Palette.blue)
            if model.busy { ProgressView("One moment…") }
            if !model.transcript.isEmpty {
                Text("You said: \(model.transcript)").font(.callout).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
    }

    private var compactVoiceSection: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Let's find your favourites.")
                    .font(.system(.title2, design: .rounded, weight: .semibold))
                Text("You can change your mind anytime.").font(.subheadline).foregroundStyle(Palette.slate)
            }
            Spacer(minLength: 0)
            Button { Task { await model.toggleRecording() } } label: {
                Image(systemName: "mic.fill").font(.title2).foregroundStyle(.white)
                    .frame(width: 60, height: 60).background(Palette.blue, in: Circle())
            }
            .disabled(model.busy)
            .accessibilityLabel("Record grocery request")
        }
    }

    private var assistantSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("EasyDial", systemImage: "waveform").font(.headline)
                Spacer()
                if model.config?.voiceAvailable == true {
                    Button { Task { await model.speak() } } label: {
                        Image(systemName: "speaker.wave.2").frame(minWidth: 44, minHeight: 44)
                    }.accessibilityLabel("Read response aloud")
                    Button { model.voice.stopPlayback() } label: {
                        Image(systemName: "stop.circle").frame(minWidth: 44, minHeight: 44)
                    }.accessibilityLabel("Stop speech playback")
                }
            }
            Text(model.session?.lastReply ?? "").font(.body).fixedSize(horizontal: false, vertical: true)
            if model.config?.voiceAvailable == true {
                Text("Voice replies are AI-generated.").font(.caption).foregroundStyle(Palette.slate)
            }
        }
        .padding(18).background(Palette.sky, in: RoundedRectangle(cornerRadius: 20))
    }

    private var searchSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            if model.config?.addressConfigured == true {
                Text("Try an everyday essential").font(.subheadline.weight(.medium)).foregroundStyle(Palette.slate)
                HStack(spacing: 10) {
                    ForEach(["Milk", "Bread", "Rice"], id: \.self) { query in
                        Button { Task { await model.search(query) } } label: {
                            Text(query).frame(maxWidth: .infinity)
                        }
                            .buttonStyle(SoftActionStyle())
                            .accessibilityIdentifier("search\(query)")
                    }
                }
            }
            HStack(alignment: .bottom) {
                TextField(model.config?.conversationAvailable == true ? "Type a request or correction" : "Search by product name", text: $model.draft, axis: .vertical)
                    .lineLimit(1...4).padding(.leading, 16).padding(.vertical, 16)
                    .accessibilityIdentifier("requestField")
                Button {
                    Task {
                        if model.config?.conversationAvailable == true { await model.sendText(model.draft) }
                        else { await model.search(model.draft) }
                    }
                } label: {
                    Image(systemName: "arrow.up").font(.headline)
                        .foregroundStyle(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? Palette.blue.opacity(0.4) : Color.white)
                        .frame(width: 44, height: 44)
                        .background(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? Palette.sky : Palette.blue, in: RoundedRectangle(cornerRadius: 12))
                }
                .disabled(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("Send request")
                .padding(6)
            }
            .background(.white, in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(Palette.blue.opacity(0.12), lineWidth: 1))
        }
        .disabled(model.busy || model.voice.isRecording)
    }

    private var productGroups: [[Offer]] {
        var groups: [[Offer]] = []
        var keys: [String] = []
        for offer in model.session?.results.flatMap({ $0.offers }) ?? [] {
            let key: String
            if let brand = offer.brand, let variant = offer.variant, let size = offer.packSize {
                key = "\(brand)|\(variant)|\(size)|\(offer.suggestedQuantity)|\(offer.replacesLineId ?? "")".lowercased()
            } else { key = offer.id }
            if let index = keys.firstIndex(of: key) { groups[index].append(offer) }
            else { keys.append(key); groups.append([offer]) }
        }
        return groups
    }

    private var resultsSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            if let session = model.session, !session.results.isEmpty {
                HStack {
                    Text(model.replaceLineID == nil ? "Choose your groceries" : "Choose a replacement")
                        .font(.system(.title2, design: .rounded, weight: .semibold))
                    Spacer()
                    if model.replaceLineID != nil { Button("Cancel") { model.replaceLineID = nil } }
                }
                ForEach(Array(session.results.enumerated()), id: \.offset) { _, result in
                    if result.status != "ok" {
                        Text("\(result.provider.capitalized): \(result.message ?? "Unavailable")")
                            .font(.callout).foregroundStyle(Palette.slate)
                    }
                    if result.truncated { Text("Showing a limited selection. Try a more specific search.").font(.footnote) }
                }
                ForEach(Array(productGroups.enumerated()), id: \.offset) { _, offers in
                    if let first = offers.first {
                        VStack(alignment: .leading, spacing: 16) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(first.name).font(.headline)
                                Text("\(first.packSize ?? "Pack size unverified") · \(first.suggestedQuantity) pack\(first.suggestedQuantity == 1 ? "" : "s")")
                                    .font(.subheadline).foregroundStyle(Palette.slate)
                            }
                            ForEach(offers) { offer in
                                HStack(spacing: 12) {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(offer.store).font(.subheadline.weight(.medium))
                                        Text(rupees(offer.pricePaise)).font(.title3.weight(.semibold)).foregroundStyle(Palette.blue)
                                        if offer.available != true {
                                            Text(offer.available == false ? "Out of stock" : "Availability unverified")
                                                .font(.caption).foregroundStyle(Palette.slate)
                                        }
                                    }
                                    Spacer(minLength: 0)
                                    Button(model.replaceLineID == nil && offer.replacesLineId == nil ? "Add \(offer.suggestedQuantity)" : "Replace") {
                                        let target = model.replaceLineID ?? offer.replacesLineId
                                        Task { await model.edit(kind: target == nil ? "add" : "replace",
                                                                offerID: offer.id, lineID: target, quantity: offer.suggestedQuantity) }
                                    }
                                    .buttonStyle(SoftActionStyle())
                                    .disabled(model.busy || offer.available != true || offer.pricePaise == nil)
                                    .accessibilityLabel("\(model.replaceLineID == nil ? "Add" : "Replace with") \(offer.name) from \(offer.store)")
                                    .accessibilityIdentifier("add-\(offer.id)")
                                }
                                if offer.id != offers.last?.id { Divider() }
                            }
                            Text("Item prices only. Delivery fees not included.")
                                .font(.caption).foregroundStyle(Palette.slate)
                        }
                        .padding(20).background(.white, in: RoundedRectangle(cornerRadius: 22))
                        .overlay(RoundedRectangle(cornerRadius: 22).stroke(Palette.sky, lineWidth: 1))
                    }
                }
            }
        }
    }

    private var basketSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            if let session = model.session {
                    Text("Everything you need.").font(.system(.title2, design: .rounded, weight: .semibold))
                ForEach(session.lines) { line in
                    VStack(alignment: .leading, spacing: 12) {
                        Text(line.offer.name).font(.headline)
                        Text("\(line.offer.packSize ?? "") · \(line.offer.store)").font(.subheadline).foregroundStyle(Palette.slate)
                        HStack {
                            Button {
                                Task { await model.edit(kind: line.quantity == 1 ? "remove" : "set_quantity", lineID: line.id, quantity: max(1, line.quantity - 1)) }
                            } label: { Image(systemName: "minus").frame(width: 44, height: 44) }
                            .buttonStyle(.plain).foregroundStyle(Palette.blue)
                            .background(Palette.sky, in: RoundedRectangle(cornerRadius: 12)).accessibilityLabel("Decrease \(line.offer.name)")
                            Text("\(line.quantity)").font(.title3.bold()).frame(minWidth: 28)
                                .accessibilityIdentifier("quantity-\(line.offer.id)")
                            Button {
                                Task { await model.edit(kind: "set_quantity", lineID: line.id, quantity: line.quantity + 1) }
                            } label: { Image(systemName: "plus").frame(width: 44, height: 44) }
                            .buttonStyle(.plain).foregroundStyle(Palette.blue)
                            .background(Palette.sky, in: RoundedRectangle(cornerRadius: 12)).accessibilityLabel("Increase \(line.offer.name)")
                            .disabled(line.quantity >= line.offer.maxQuantity)
                            Spacer()
                            Text(rupees((line.offer.pricePaise ?? 0) * line.quantity)).font(.headline)
                        }
                        HStack {
                            Button("Replace") { model.replaceLineID = line.id }
                            Spacer()
                            Button("Remove", role: .destructive) {
                                Task { await model.edit(kind: "remove", lineID: line.id) }
                            }
                        }.frame(minHeight: 44)
                    }
                    Divider()
                }
                HStack {
                    Text("Item subtotal").font(.headline)
                    Spacer()
                    Text(rupees(session.subtotal)).font(.title2.bold())
                }
                Text("Review sends these items to your store basket and checks the latest bill. Payment stays pending.").font(.footnote).foregroundStyle(Palette.slate)
                if !session.lines.isEmpty || session.syncedProvider != nil {
                    Button(model.busy ? "Checking store basket…" : (session.lines.isEmpty ? "Sync empty basket" : "Review basket")) {
                        Task { await model.review() }
                    }
                    .buttonStyle(PrimaryActionStyle())
                    .accessibilityIdentifier("reviewBasket")
                }
            }
        }
        .disabled(model.busy || model.voice.isRecording)
    }

    private var reviewSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            if let review = model.session?.review {
                Text("Your live bill").font(.system(.title2, design: .rounded, weight: .semibold))
                Text(review.provider == "instamart" ? "Swiggy Instamart" : "Blinkit")
                    .font(.headline).foregroundStyle(Palette.blue)
                Label(review.addressLabel, systemImage: "location").font(.subheadline)
                ForEach(Array(review.billLines.enumerated()), id: \.offset) { _, line in
                    HStack { Text(line.label); Spacer(); Text(line.value) }.font(.subheadline)
                }
                Divider()
                HStack { Text("To pay"); Spacer(); Text(rupees(review.totalPaise)) }.font(.title3.bold())
                if model.session?.phase == "checkout_pending" {
                    Label("Pending", systemImage: "clock")
                        .font(.headline).foregroundStyle(Palette.blue)
                        .accessibilityIdentifier("checkoutPending")
                    Text("Payment isn't enabled. Your items are in the store basket. No order has been placed.")
                        .font(.subheadline).foregroundStyle(Palette.slate)
                } else {
                    Button("Checkout") { Task { await model.confirm() } }
                        .buttonStyle(PrimaryActionStyle())
                        .disabled(model.busy).accessibilityIdentifier("checkout")
                    Text("Stops at pending. No payment or order will be submitted.")
                        .font(.footnote).foregroundStyle(Palette.slate)
                }
            }
        }
    }

    private var settingsView: some View {
        NavigationStack {
            Form {
                Section("Development connection") {
                    TextField("Server URL", text: $model.backendURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    Text("Simulator: http://127.0.0.1:8000. On an iPhone, use your Mac's local hostname on the same network.")
                        .font(.footnote)
                    Button("Connect and start a new session") {
                        settings = false
                        Task { await model.connect() }
                    }.disabled(model.busy)
                }
                Section("This build") {
                    LabeledContent("Commerce", value: model.config?.mode.capitalized ?? "Disconnected")
                    LabeledContent("Voice", value: model.config?.voiceAvailable == true ? "Connected" : "Not connected")
                    LabeledContent("Blinkit", value: model.config?.providers["blinkit"] == true ? "Configured" : "Needs connection")
                    LabeledContent("Instamart", value: model.config?.providers["instamart"] == true ? "Configured" : "Needs connection")
                    Text("Live search and store baskets. Checkout stops at Pending; payment and order placement are disabled. Configure connections on your Mac using docs/CREDENTIALS.md, then reconnect here.")
                }
            }
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { settings = false } } }
        }
    }
}

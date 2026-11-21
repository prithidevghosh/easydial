import SwiftUI

private enum Palette {
    static let blue = Color(red: 0.075, green: 0.227, blue: 0.447)
    static let paper = Color(red: 0.973, green: 0.98, blue: 0.992)
    static let sky = Color(red: 0.91, green: 0.941, blue: 0.988)
    static let ink = Color(red: 0.094, green: 0.149, blue: 0.239)
}

struct ContentView: View {
    @State private var model = ShoppingModel()
    @State private var settings = false
    @State private var showBasket = false
    @State private var showConfirmation = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 28) {
                        if model.session?.mode == "sample" { sampleBanner }
                        if model.session?.phase == "sample_complete" {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Sample journey complete").font(.title2.bold())
                                Text("No real order was placed. Ready to try another basket?")
                                Button("Start a new journey") { Task { await model.connect() } }
                                    .buttonStyle(.borderedProminent).controlSize(.large)
                            }
                        }
                        if model.session == nil { connectionView } else {
                            if model.session?.results.isEmpty == true || model.voice.isRecording {
                                voiceSection
                            } else {
                                compactVoiceSection
                            }
                            assistantSection
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
                    .padding(.bottom, 90)
                }
                .background(Palette.paper)
                .safeAreaInset(edge: .bottom) {
                    if let session = model.session, !session.lines.isEmpty, session.phase != "sample_complete" {
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
                    .confirmationDialog("Complete this sample journey?", isPresented: $showConfirmation, titleVisibility: .visible) {
                        Button("Confirm sample checkout") { Task { await model.confirm() } }
                    } message: { Text("No payment will be taken and no real order will be placed.") }
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
        .foregroundStyle(Palette.ink)
    }

    private var sampleBanner: some View {
        Label("Sample prices · No real orders or payments", systemImage: "info.circle")
        .font(.footnote.weight(.medium))
        .foregroundStyle(Palette.blue)
        .padding(.horizontal, 14).padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.sky.opacity(0.65), in: RoundedRectangle(cornerRadius: 12))
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
                    .buttonStyle(.borderedProminent).controlSize(.large)
            }
        }
        .padding(.vertical, 24)
    }

    private var voiceSection: some View {
        VStack(spacing: 20) {
            Text(model.voice.isRecording ? "I'm listening." : "What can we get\nyou today?")
                .font(.system(.largeTitle, design: .rounded, weight: .semibold))
                .multilineTextAlignment(.center)
            Text(model.voice.isRecording ? "Take your time. Tap again when you're done." : "Milk, bread, or your everyday essentials.\nJust say it your way.")
                .font(.body).multilineTextAlignment(.center).foregroundStyle(.secondary)
            Button { Task { await model.toggleRecording() } } label: {
                ZStack {
                    Circle().stroke(Palette.blue.opacity(0.08), lineWidth: 1).frame(width: 184, height: 184)
                    Circle().fill(Palette.sky).frame(width: 158, height: 158)
                    Circle().fill(model.voice.isRecording ? Color.red : Palette.blue).frame(width: 120, height: 120)
                        .shadow(color: Palette.blue.opacity(0.16), radius: 14, y: 8)
                    Image(systemName: model.voice.isRecording ? "stop.fill" : "mic.fill")
                        .font(.system(size: 42, weight: .medium)).foregroundStyle(.white)
                }
            }
            .disabled(model.busy || model.session?.phase == "sample_complete")
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
        .padding(.top, 12)
    }

    private var compactVoiceSection: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Let's find your favourites.")
                    .font(.system(.title2, design: .rounded, weight: .semibold))
                Text("You can change your mind anytime.").font(.subheadline).foregroundStyle(.secondary)
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
                Text("Voice replies are AI-generated.").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(18).background(Palette.sky, in: RoundedRectangle(cornerRadius: 20))
    }

    private var searchSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            if model.session?.mode == "sample" {
                Text("Or try a sample search").font(.headline)
                HStack(spacing: 10) {
                    ForEach(["Milk", "Bread", "Rice"], id: \.self) { query in
                        Button(query) { Task { await model.search(query) } }
                            .buttonStyle(.bordered).controlSize(.large)
                            .accessibilityIdentifier("search\(query)")
                    }
                }
            }
            HStack(alignment: .bottom) {
                TextField(model.config?.conversationAvailable == true ? "Type a request or correction" : "Search by product name", text: $model.draft, axis: .vertical)
                    .lineLimit(1...4).textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("requestField")
                Button {
                    Task {
                        if model.config?.conversationAvailable == true { await model.sendText(model.draft) }
                        else { await model.search(model.draft) }
                    }
                } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.system(size: 36))
                        .frame(minWidth: 44, minHeight: 44)
                }
                .disabled(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("Send request")
            }
        }
        .disabled(model.busy || model.voice.isRecording || model.session?.phase == "sample_complete")
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
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if result.truncated { Text("Showing a limited selection. Try a more specific search.").font(.footnote) }
                }
                ForEach(Array(productGroups.enumerated()), id: \.offset) { _, offers in
                    if let first = offers.first {
                        VStack(alignment: .leading, spacing: 16) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(first.name).font(.headline)
                                Text("\(first.packSize ?? "Pack size unverified") · \(first.suggestedQuantity) pack\(first.suggestedQuantity == 1 ? "" : "s")")
                                    .font(.subheadline).foregroundStyle(.secondary)
                            }
                            ForEach(offers) { offer in
                                HStack(spacing: 12) {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(offer.store).font(.subheadline.weight(.medium))
                                        Text(rupees(offer.pricePaise)).font(.title3.weight(.semibold)).foregroundStyle(Palette.blue)
                                        if offer.available != true {
                                            Text(offer.available == false ? "Out of stock" : "Availability unverified")
                                                .font(.caption).foregroundStyle(.secondary)
                                        }
                                    }
                                    Spacer(minLength: 0)
                                    Button(model.replaceLineID == nil && offer.replacesLineId == nil ? "Add \(offer.suggestedQuantity)" : "Replace") {
                                        let target = model.replaceLineID ?? offer.replacesLineId
                                        Task { await model.edit(kind: target == nil ? "add" : "replace",
                                                                offerID: offer.id, lineID: target, quantity: offer.suggestedQuantity) }
                                    }
                                    .buttonStyle(.bordered).controlSize(.large)
                                    .disabled(model.busy || offer.available != true || offer.pricePaise == nil || session.phase == "sample_complete")
                                    .accessibilityLabel("\(model.replaceLineID == nil ? "Add" : "Replace with") \(offer.name) from \(offer.store)")
                                    .accessibilityIdentifier("add-\(offer.id)")
                                }
                                if offer.id != offers.last?.id { Divider() }
                            }
                            Text(first.mode == "sample" ? "Sample item prices. Delivery fees not included." : "Item prices only. Delivery fees not included.")
                                .font(.caption).foregroundStyle(.secondary)
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
            if let session = model.session, !session.lines.isEmpty {
                    Text("Everything you need.").font(.system(.title2, design: .rounded, weight: .semibold))
                ForEach(session.lines) { line in
                    VStack(alignment: .leading, spacing: 12) {
                        Text(line.offer.name).font(.headline)
                        Text("\(line.offer.packSize ?? "") · \(line.offer.store)").font(.subheadline).foregroundStyle(.secondary)
                        HStack {
                            Button {
                                Task { await model.edit(kind: line.quantity == 1 ? "remove" : "set_quantity", lineID: line.id, quantity: max(1, line.quantity - 1)) }
                            } label: { Image(systemName: "minus").frame(width: 44, height: 44) }
                            .buttonStyle(.bordered).accessibilityLabel("Decrease \(line.offer.name)")
                            Text("\(line.quantity)").font(.title3.bold()).frame(minWidth: 28)
                                .accessibilityIdentifier("quantity-\(line.offer.id)")
                            Button {
                                Task { await model.edit(kind: "set_quantity", lineID: line.id, quantity: line.quantity + 1) }
                            } label: { Image(systemName: "plus").frame(width: 44, height: 44) }
                            .buttonStyle(.bordered).accessibilityLabel("Increase \(line.offer.name)")
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
                Text("Delivery and other fees are confirmed at checkout.").font(.footnote).foregroundStyle(.secondary)
                if session.phase != "sample_complete" {
                    Button(session.mode == "sample" ? "Review sample basket" : "Review basket") {
                        Task { await model.review() }
                    }
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .accessibilityIdentifier("reviewBasket")
                }
            }
        }
        .disabled(model.busy || model.voice.isRecording || model.session?.phase == "sample_complete")
    }

    private var reviewSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let review = model.session?.review {
                Text(model.session?.phase == "sample_complete" ? "You're all set for this sample." : "Check before you confirm")
                    .font(.title2.bold())
                Text(review.addressLabel)
                HStack { Text("Sample total"); Spacer(); Text(rupees(review.totalPaise)).bold() }
                Text(review.paymentMethod).font(.callout)
                if model.session?.phase == "sample_complete" {
                    Label("No real order was placed.", systemImage: "checkmark.circle.fill").foregroundStyle(Palette.blue)
                    Button("Start a new journey") {
                        showBasket = false
                        Task { await model.connect() }
                    }
                        .buttonStyle(.borderedProminent).controlSize(.large)
                } else {
                    Button("Complete sample journey") { showConfirmation = true }
                        .buttonStyle(.borderedProminent).controlSize(.large)
                        .disabled(model.busy).accessibilityIdentifier("completeSample")
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
                    Text("Live purchasing is not enabled. Configure API credentials on the backend, never in the app.")
                }
            }
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { settings = false } } }
        }
    }
}

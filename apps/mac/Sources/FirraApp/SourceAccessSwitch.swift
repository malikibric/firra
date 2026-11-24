import FirraKit
import SwiftUI

struct SourceAccessSwitch: View {
  @Bindable var model: SourceAccessModel
  @State private var presentsConfirmation = false
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Toggle(model.title, isOn: Binding(get: { model.enabled }, set: { value in
        Task {
          await model.requestChange(to: value)
          presentsConfirmation = value && model.confirmationRequired
        }
      })).disabled(!model.loaded || model.loading || model.saving || model.confirmationRequired)
      Text(model.explanation).font(.caption)
      if model.loading { ProgressView("Loading \(model.settingName.lowercased())…") }
      if model.saving { ProgressView("Saving \(model.settingName.lowercased())…") }
      if let message = model.message {
        Text(message).foregroundStyle(.orange)
        Button("Retry") { Task { await model.load() } }.disabled(model.loading || model.saving)
      }
    }.task { await model.load() }
      // Only the screen that requested access presents the sheet, even if both screens are mounted.
      .sheet(isPresented: Binding(get: { presentsConfirmation && model.confirmationRequired }, set: { presented in
        if !presented { presentsConfirmation = false; model.cancelConfirmation() }
      })) {
        VStack(alignment: .leading, spacing: 16) {
          Text(model.title).font(.headline)
          Text(model.explanation)
          HStack {
            Button("Cancel", role: .cancel) { model.cancelConfirmation() }
            Button(model.confirmationLabel) { Task { await model.confirm() } }
          }
        }.padding(24).frame(width: 500)
      }
  }
}

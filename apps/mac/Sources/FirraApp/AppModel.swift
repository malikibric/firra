import FirraKit
import Foundation
import Observation
import AppKit

/// Starts the local engine and builds the screens only after its owner identity is loaded.
@MainActor @Observable
final class AppModel {
  struct Services {
    let sourceAccess: SourceAccessModel
    let processingTools: SourceAccessModel
    let slack: SlackConnectModel
    let data: SettingsDataModel
    let aiSettings: AISetupModel
    let menu: MenuStatusModel
    let onboarding: OnboardingModel
    let owner: UserProfile
    let workspaceId: String
    let attention: AttentionModel
    let notch: NotchModel
    let clients: ClientsModel
    let connections: ConnectionsModel
    let clientDetail: ClientDetailModel
    let decisions: DecisionsModel
    let audit: AuditModel
    let replay: ReplayModel
    let services: ServicesModel
    let routines: RoutinesModel
    let api: APIClient
    let cache: FileResponseCache
  }

  struct ClientTarget: Identifiable { let id: String; let name: String }
  private(set) var wiping = false
  private(set) var maintenance: SettingsDataModel?
  var requestedClient: ClientTarget?
  var requestedAction: String?
  private var notifier: Notifier?
  let local: LocalEngineModel
  let updates: UpdateModel
  let loginItem = LoginItemModel(actions: .system)
  private(set) var services: Services?
  private let supervisor: EngineSupervisor
  private let config: FirraConfig
  private let secrets: KeychainEngineSecrets
  private var startupError: String?
  private var observer: Task<Void, Never>?
  private var refresher: Task<Void, Never>?
  private var notchController: NotchController?
  #if DEBUG
    private var demo: NotchDemo?
  #endif

  var notchEnabled: Bool {
    didSet {
      UserDefaults.standard.set(notchEnabled, forKey: "notch.enabled")
      updateNotch()
    }
  }

  init() {
    UserDefaults.standard.register(defaults: ["notch.enabled": true])
    notchEnabled = UserDefaults.standard.bool(forKey: "notch.enabled")
    let config = FirraConfig.load()
    self.config = config
    let secrets = KeychainEngineSecrets()
    self.secrets = secrets
    let control = (try? EngineSupervisor.controlDirectory(home: config.home))
      ?? config.home.appending(path: "run")
    let supervisor = EngineSupervisor(home: config.home, controlDirectory: control,
      appVersion: config.buildID,
      installer: EngineInstaller(bundledEngine: config.bundledEngine, home: config.home,
                                 buildID: config.buildID), secrets: secrets)
    self.supervisor = supervisor
    local = LocalEngineModel(restart: {
      await supervisor.stop()
      await supervisor.start()
    })
    do { try secrets.ensureDatabaseKey() } catch {
      startupError = "The local key could not be opened (\(EngineSupervisor.describe(error)))."
    }
    // The release address is baked in at build time (FIRRA_UPDATE_FEED); without it the check is off.
    let feed = (Bundle.main.infoDictionary?["FirraUpdateFeed"] as? String).flatMap(URL.init(string:))
    let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    let last = UserDefaults.standard.object(forKey: "update.lastChecked") as? Date
    updates = UpdateModel(feed: feed, currentVersion: version, lastChecked: last) { url in
      var request = URLRequest(url: url, timeoutInterval: 15)
      request.httpShouldHandleCookies = false
      let (data, response) = try await URLSession(configuration: .ephemeral).data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw APIError.transport("feed") }
      return data
    }
  }

  func start() {
    guard observer == nil else { return }
    #if DEBUG
      if NotchDemo.requested, demo == nil { demo = NotchDemo() }
    #endif
    Task {
      await updates.checkIfDue()
      if let checked = updates.checkedAt { UserDefaults.standard.set(checked, forKey: "update.lastChecked") }
    }
    observer = Task {
      if let startupError {
        await local.receive(.failed(startupError))
        return
      }
      let states = await supervisor.states()
      await supervisor.start()
      for await state in states {
        if Task.isCancelled { break }
        if wiping { continue }
        if case .running = state, state == local.state, services != nil { continue }
        retireServices()
        await local.receive(state)
        if case .running = local.state, let api = local.api,
          let me = local.me, let workspace = local.workspaceId {
          if services == nil { makeServices(api: api, owner: me.user, workspace: workspace) }
        } else { retireServices() }
      }
    }
  }

  func restart() { guard !wiping else { return }; Task { await local.restart() } }
  func stop() async {
    observer?.cancel()
    observer = nil
    retireServices()
    await supervisor.stop()
  }

  private func makeServices(api: APIClient, owner: UserProfile, workspace: String) {
    let cache = FileResponseCache()
    cache.use(user: owner.id)
    let attention = AttentionModel(api: api, cache: cache)
    let detail = ClientDetailModel(api: api)
    let decisions = DecisionsModel(api: api)
    let clients = ClientsModel(api: api)
    clients.onClientRestricted = { clientId in
      cache.clearAll()
      attention.forget(clientId: clientId)
      detail.reset()
      decisions.reset()
    }
    let sourceAccess = SourceAccessModel(api: api)
    let processingTools = SourceAccessModel(api: api, setting: .processingTools)
    let onboarding = OnboardingModel(api: api, workspace: workspace, sourceAccess: sourceAccess,
      authorize: { [config] in
        let oauth = try loadGoogleOAuthConfig(home: config.home)
        return try await GoogleDesktopAuth(config: oauth, opener: SystemBrowser()).signIn()
      }, putSecret: { [supervisor] token, slot in try await supervisor.putSecret(token, for: slot) })
    let menu = MenuStatusModel(api: api, workspace: workspace, owner: owner.id)
    let data = makeDataModel(api: api)
    maintenance = data
    let services = Services(sourceAccess: sourceAccess, processingTools: processingTools, slack: SlackConnectModel(api: api, workspace: workspace, putSecret: { [supervisor] token, slot in
      try await supervisor.putSecret(token, for: slot)
    }), data: data, aiSettings: AISetupModel(api: api), menu: menu, onboarding: onboarding, owner: owner, workspaceId: workspace, attention: attention,
      notch: NotchModel(attention: attention, meetings: APIMeetingBriefs(api: api)),
      clients: clients, connections: ConnectionsModel(api: api, reconnect: { [weak self] connection in
        try await self?.reconnect(connection, api: api)
      }), clientDetail: detail, decisions: decisions, audit: AuditModel(api: api),
      replay: ReplayModel(api: api), services: ServicesModel(api: api),
      routines: RoutinesModel(api: api), api: api, cache: cache)
    self.services = services
    updateNotch()
    let notifier = Notifier(focus: menu.focus, openClient: { [weak self] id, name in self?.showClient(id, name: name) },
      openAction: { [weak self] action in
        self?.showAction(action)
      })
    self.notifier = notifier
    notifier.requestPermissionOnce()
    refresher = Task {
      while !Task.isCancelled {
        let notices = await menu.pollIfDue(active: NSApplication.shared.isActive)
        guard !Task.isCancelled else { return }
        notifier.notice(notices)
        if notices.refreshed && menu.health == .running { await attention.load(workspaceId: workspace) }
        try? await Task.sleep(for: .seconds(15))
      }
    }
  }

  private func makeDataModel(api: APIClient) -> SettingsDataModel {
    SettingsDataModel(api: api, actions: SettingsDataActions(
      savePanel: SettingsPanels.save, openPanel: SettingsPanels.openBackup,
      read: SettingsDataFiles.readBackup, write: { try SettingsDataFiles.write($0, to: $1) },
      copy: { value in
        NSPasteboard.general.clearContents()
        return NSPasteboard.general.setString(value, forType: .string)
      }, prepareDeletion: { [weak self] in self?.prepareDeletion() },
      deleteKeychain: { try FirraKeychain.deleteAll() },
      stopEngine: { [supervisor] in await supervisor.stop() },
      deleteFiles: { [config] in
        try await Task.detached { try SettingsDataFiles.deleteHome(config.home) }.value
      }, resetDefaults: {
        guard let identifier = Bundle.main.bundleIdentifier else { throw CocoaError(.fileWriteUnknown) }
        UserDefaults.standard.removePersistentDomain(forName: identifier)
      }, quit: { NSApplication.shared.terminate(nil) }))
  }
  private func prepareDeletion() {
    wiping = true
    local.suspendForDeletion()
    refresher?.cancel(); refresher = nil
    services?.notch.stop(); notchController?.stop(); notchController = nil
    services?.cache.use(user: nil)
    notifier = nil
    requestedClient = nil; requestedAction = nil
  }

  func showClient(_ id: String, name: String) {
    requestedClient = ClientTarget(id: id, name: name)
    NotificationCenter.default.post(name: .firraOpenMain, object: nil)
  }
  func showAction(_ action: String) {
    services?.menu.focus.actionKinds.insert(action)
    requestedAction = action
    NotificationCenter.default.post(name: .firraOpenMain, object: nil)
  }
  func dismissAction() {
    if let action = requestedAction { services?.menu.focus.actionKinds.remove(action) }
    requestedAction = nil
  }
  func reconnectGoogle() {
    guard let services, let connection = services.menu.reconnectConnection else { return }
    Task { await services.connections.reconnect(workspaceId: services.workspaceId, connection: connection) }
  }
  var menuTitle: String {
    if wiping { return "Deleting Firra data" }
    switch local.state {
    case .running: return services?.menu.title ?? "Firra is starting"
    case .stopped, .failed: return "Firra stopped"
    default: return "Firra is starting"
    }
  }
  var menuIcon: String {
    if wiping { return "trash" }
    switch local.state {
    case .running: return services?.menu.icon ?? "clock"
    case .stopped, .failed: return "exclamationmark.triangle"
    default: return "clock"
    }
  }

  private func reconnect(_ connection: LocalStatus.Connection, api: APIClient) async throws {
    let oauth = try loadGoogleOAuthConfig(home: config.home)
    let grant = try await GoogleDesktopAuth(config: oauth, opener: SystemBrowser()).signIn(
      loginHint: connection.accountLabel)
    // The setup API expects the Gmail/root UUID even when reconnecting its calendar sibling.
    let status = try await api.localStatus()
    let root = status.connections.first {
      $0.provider == "gmail" && $0.accountLabel == connection.accountLabel
    }?.id ?? connection.id
    try await supervisor.putSecret(grant.refreshToken, for: SecretSlot.googleRefreshToken(root))
    try await api.connectGoogle(connectionId: root, accountLabel: grant.email)
  }

  private func retireServices() {
    refresher?.cancel()
    refresher = nil
    notifier = nil
    requestedClient = nil
    requestedAction = nil
    services?.notch.stop()
    notchController?.stop()
    notchController = nil
    services?.cache.retire()
    services = nil
  }

  private func updateNotch() {
    guard let services else { return }
    if notchEnabled {
      guard notchController == nil else { return }
      let workspace = services.workspaceId
      services.notch.start { workspace }
      let controller = NotchController(model: services.notch)
      notchController = controller
      controller.start()
    } else {
      services.notch.stop()
      notchController?.stop()
      notchController = nil
    }
  }
}

private struct SystemBrowser: URLOpening {
  func open(_ url: URL) async -> Bool {
    await MainActor.run { NSWorkspace.shared.open(url) }
  }
}

/* Generated UI — modules: command_center, estate_registry, maintenance, readiness, portfolio, evidence */
import CommandCenterModule from './modules/command_center';
import EstateRegistryModule from './modules/estate_registry';
import MaintenanceModule from './modules/maintenance';
import ReadinessModule from './modules/readiness';
import PortfolioModule from './modules/portfolio';
import EvidenceModule from './modules/evidence';
export default function App(){
  return (
    <main>
      <h1>Cerebrum Steward</h1>
      <CommandCenterModule />
      <EstateRegistryModule />
      <MaintenanceModule />
      <ReadinessModule />
      <PortfolioModule />
      <EvidenceModule />
    </main>
  )
}
